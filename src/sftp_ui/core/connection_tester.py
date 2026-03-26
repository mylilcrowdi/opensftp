"""
Connection tester — quick connectivity check without opening a full session.

Tries an SSH handshake (SFTP) or a HEAD bucket call (S3/GCS) and returns
a (success: bool, message: str) tuple. Always cleans up after itself.
"""
from __future__ import annotations

import socket
from typing import Optional

import paramiko

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError
    _HAS_BOTO3 = True
except ImportError:
    boto3 = None  # type: ignore[assignment]
    _HAS_BOTO3 = False


def test_sftp_connection(
    *,
    host: str,
    port: int = 22,
    user: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    key_passphrase: Optional[str] = None,
    use_agent: bool = False,
    timeout: int = 10,
) -> tuple[bool, str]:
    """Try an SSH handshake and return (ok, message)."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    kwargs: dict = dict(
        hostname=host,
        port=port,
        username=user,
        timeout=timeout,
        look_for_keys=False,
        allow_agent=use_agent,
    )

    if key_path:
        try:
            pkey = paramiko.RSAKey.from_private_key_file(key_path, password=key_passphrase)
        except paramiko.ssh_exception.SSHException:
            try:
                pkey = paramiko.Ed25519Key.from_private_key_file(key_path, password=key_passphrase)
            except Exception:
                try:
                    pkey = paramiko.ECDSAKey.from_private_key_file(key_path, password=key_passphrase)
                except Exception as exc:
                    return False, f"Cannot load key: {exc}"
        kwargs["pkey"] = pkey
    elif password:
        kwargs["password"] = password
    elif not use_agent:
        return False, "No authentication method provided (no key, no password, no agent)"

    try:
        ssh.connect(**kwargs)
        ssh.close()
        return True, "Connection successful"
    except paramiko.AuthenticationException as exc:
        ssh.close()
        return False, f"Authentication failed: {exc}"
    except socket.timeout:
        ssh.close()
        return False, "Connection timed out"
    except socket.error as exc:
        ssh.close()
        return False, f"Connection error: {exc}"
    except Exception as exc:
        ssh.close()
        return False, f"Error: {exc}"


def test_cloud_connection(
    *,
    provider: str = "s3",
    bucket: str,
    access_key: str = "",
    secret_key: str = "",
    region: str = "",
    endpoint_url: str = "",
    timeout: int = 10,
) -> tuple[bool, str]:
    """Try a HEAD bucket call and return (ok, message)."""
    if not _HAS_BOTO3:
        return False, "boto3 is not installed"

    config = BotoConfig(connect_timeout=timeout, read_timeout=timeout)

    client_kwargs: dict = {"config": config}
    if access_key and secret_key:
        client_kwargs["aws_access_key_id"] = access_key
        client_kwargs["aws_secret_access_key"] = secret_key
    if region:
        client_kwargs["region_name"] = region
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url

    try:
        client = boto3.client("s3", **client_kwargs)
        client.head_bucket(Bucket=bucket)
        return True, "Connection successful"
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        if code in ("403", "AccessDenied"):
            return False, f"Access denied: {msg}"
        if code in ("404", "NoSuchBucket"):
            return False, f"Bucket not found: {bucket}"
        return False, f"Error ({code}): {msg}"
    except NoCredentialsError:
        return False, "No credentials provided"
    except EndpointConnectionError as exc:
        return False, f"Cannot reach endpoint: {exc}"
    except Exception as exc:
        return False, f"Error: {exc}"
