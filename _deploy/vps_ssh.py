"""VPS 部署辅助：封装 SSH/SCP 操作，密码从环境变量 VPS_PWD 读，避免命令行泄漏。"""
import os
import sys
import stat
import posixpath
import paramiko

HOST = "199.30.90.251"
USER = "root"
PWD = os.environ["VPS_PWD"]


def _client():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=30,
              allow_agent=False, look_for_keys=False)
    return c


def run(cmd, timeout=120):
    """执行远程命令，返回 (rc, stdout, stderr)。"""
    c = _client()
    try:
        stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err
    finally:
        c.close()


def put(local, remote):
    """sftp 上传单个文件。"""
    c = _client()
    try:
        sftp = c.open_sftp()
        sftp.put(local, remote)
        sftp.close()
    finally:
        c.close()


def put_dir(local_dir, remote_dir, skip_suffix=(".pyc",)):
    """递归上传整个目录（sftp）。"""
    c = _client()
    try:
        sftp = c.open_sftp()
        _mkdir_p(sftp, remote_dir)
        n = 0
        for root, dirs, files in os.walk(local_dir):
            rel = os.path.relpath(root, local_dir).replace("\\", "/")
            rdir = remote_dir if rel == "." else posixpath.join(remote_dir, rel)
            _mkdir_p(sftp, rdir)
            for f in files:
                if f.endswith(skip_suffix):
                    continue
                lp = os.path.join(root, f)
                rp = posixpath.join(rdir, f)
                sftp.put(lp, rp)
                n += 1
        sftp.close()
        return n
    finally:
        c.close()


def _mkdir_p(sftp, path):
    """递归创建远程目录（忽略已存在）。"""
    if path in ("", ".", "/"):
        return
    try:
        sftp.stat(path)
        return
    except FileNotFoundError:
        pass
    parent = posixpath.dirname(path)
    if parent and parent != path:
        _mkdir_p(sftp, parent)
    try:
        sftp.mkdir(path)
    except OSError:
        pass


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "run":
        rc, out, err = run(sys.argv[2], timeout=int(sys.argv[3]) if len(sys.argv) > 3 else 120)
        sys.stdout.write(out)
        sys.stderr.write(err)
        sys.exit(rc)
    elif action == "put":
        # 远程路径从 REMOTE 环境变量读，规避 MSYS 把 /root/... 转成 D:/... 的坑
        remote = os.environ.get("REMOTE", sys.argv[3])
        put(sys.argv[2], remote)
        print(f"uploaded {sys.argv[2]} -> {remote}")
    elif action == "put_dir":
        remote = os.environ.get("REMOTE", sys.argv[3])
        n = put_dir(sys.argv[2], remote)
        print(f"uploaded {n} files -> {remote}")
