"""future_vps 部署主脚本：上传目录 + 凭证 + 验证。所有远程路径在此硬编码，绕过 MSYS 路径转换。"""
import os
import posixpath
import paramiko

HOST = "199.30.90.251"
USER = "root"
PWD = os.environ["VPS_PWD"]

LOCAL_VPS = "D:/work_ai/future_vps"
LOCAL_AUTH = "D:/work_ai/tq_auth.py"
REMOTE_VPS = "/root/future_vps"
REMOTE_AUTH = "/root/future_vps/tq_auth.py"


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=30,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()

    # 1) 清理之前误传的 /root/D: 目录
    _rmtree(sftp, c, "/root/D:")
    print("[1] cleaned /root/D: (if any)")

    # 2) 确保远程根目录存在
    _mkdir_p(sftp, REMOTE_VPS)

    # 3) 上传整个 future_vps 目录（跳过 .pyc）
    n = 0
    for root, dirs, files in os.walk(LOCAL_VPS):
        rel = os.path.relpath(root, LOCAL_VPS).replace("\\", "/")
        rdir = REMOTE_VPS if rel == "." else posixpath.join(REMOTE_VPS, rel)
        _mkdir_p(sftp, rdir)
        for f in files:
            if f.endswith(".pyc"):
                continue
            sftp.put(os.path.join(root, f), posixpath.join(rdir, f))
            n += 1
    print(f"[2] uploaded {n} files -> {REMOTE_VPS}")

    # 4) 上传真实 tq_auth.py 覆盖占位
    sftp.put(LOCAL_AUTH, REMOTE_AUTH)
    print(f"[3] uploaded real tq_auth.py -> {REMOTE_AUTH}")

    # 5) 验证关键文件
    rc, out, err = _run(c, f"ls -la {REMOTE_VPS}/ && echo '---COUNT---' && find {REMOTE_VPS} -type f | wc -l && echo '---AUTH---' && head -c 0 {REMOTE_AUTH} && grep -c TQ_USER {REMOTE_AUTH}")
    print("[4] verify:")
    print(out)
    if err.strip():
        print("STDERR:", err)

    sftp.close()
    c.close()


def _run(c, cmd, timeout=60):
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), out, err


def _mkdir_p(sftp, path):
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


def _rmtree(sftp, c, path):
    """远程递归删除目录。"""
    try:
        sftp.stat(path)
    except FileNotFoundError:
        return
    rc, out, err = _run(c, f'rm -rf -- "{path}"', timeout=60)


if __name__ == "__main__":
    main()
