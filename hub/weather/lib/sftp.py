"""
sftp.py — HAOS file delivery via SFTP for the Highland weather daemon.

Delivers generated GIF files to Home Assistant's /config/www/hub.local/
directory via SFTP using SSH key authentication.
"""

import logging
import os
from pathlib import Path

import paramiko

log = logging.getLogger(__name__)


class SftpDelivery:
    """Delivers files to HAOS via SFTP using SSH key auth."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        ssh_key_path: str,
        www_path: str = "/config/www/hub.local",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.ssh_key_path = os.path.expanduser(ssh_key_path)
        self.www_path = www_path

    def deliver(self, local_path: str, remote_subpath: str) -> str:
        """
        Upload a local file to HAOS via SFTP.

        local_path: absolute path to the file on the hub
        remote_subpath: path relative to www_path, e.g. "weather/radar/reflectivity.gif"

        Returns the remote absolute path on success.
        Raises paramiko.SSHException or OSError on failure.
        """
        remote_path = os.path.join(self.www_path, remote_subpath)
        remote_dir = str(Path(remote_path).parent)

        log.debug(f"SFTP delivering {local_path} → {self.host}:{remote_path}")

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                key_filename=self.ssh_key_path,
                timeout=30,
            )

            sftp = ssh.open_sftp()

            # Ensure remote directory exists
            _sftp_makedirs(sftp, remote_dir)

            # Upload file
            sftp.put(local_path, remote_path)
            sftp.close()

            log.info(f"SFTP delivered: {remote_path}")
            return remote_path

        finally:
            ssh.close()


def _sftp_makedirs(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    """Recursively create remote directories if they don't exist."""
    parts = Path(remote_path).parts
    current = ""
    for part in parts:
        current = os.path.join(current, part) if current else part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            try:
                sftp.mkdir(current)
            except OSError:
                pass  # May already exist due to race — ignore
