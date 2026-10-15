# PressPanel: Toàn Bộ Thiết Kỹ Thuật, Đặc Tả Kiến Trúc & Tài Liệu Triển Khai (Master Specification cho Antigravity Code)

Tài liệu này được biên soạn dưới dạng **Đặc tả Kỹ thuật Toàn diện (Master Blueprint & Technical Specification)** để nạp trực tiếp vào **Antigravity / AI Coding Engine**. Tài liệu chứa đầy đủ logic nghiệp vụ, cấu trúc mã nguồn dự án, quy hoạch thư mục triển khai, đặc tả các module C gatekeeper (chuẩn hóa không dùng tiền tố `wrap_`), cơ chế Cgroups v2 phân cấp & Disk Quota, hệ thống đóng hộp Namespace Cage tích hợp PID 1 Reaper, module **WP Manager (tương đương WP Toolkit)**, **Template Engine kế thừa 3 cấp**, **lược đồ Database SQLite mở rộng**, **đặc tả Full REST API (Domain, WP, App, File Manager, Backup, Cron, Firewall)** và **hướng dẫn sinh code theo từng giai đoạn**.

> [!NOTE]
> **Công nghệ Backend:** Toàn bộ tầng Web API Server được chuẩn hóa bằng **Python 3 (FastAPI + Uvicorn Asyncio)** kết hợp **SQLite WAL**, kế thừa trọn vẹn phong cách linh hoạt, dễ đọc, dễ mở rộng của aaPanel nhưng được bảo vệ bởi tầng nhân **8 C SUID Gatekeeper** bảo mật tuyệt đối.

## MỤC LỤC

 1. [Tổng quan Dự án & Triết lý Thiết kế](#1-tong-quan-du-an--triet-ly-thiet-ke)
 2. [Cấu trúc Mã Nguồn & Quy hoạch Thư mục Triển khai](#2-cau-truc-ma-nguon--quy-hoach-thu-muc-trien-khai)
    * 2.1. Cấu trúc Kho Mã Nguồn Phát Triển (Python FastAPI Repo Layout)
    * 2.2. Quy hoạch Thư mục Cài đặt Máy chủ (/opt/presspanel)
 3. [Kiến trúc Tách Quyền & Đặc tả 8 C Gatekeeper Modules](#3-kien-truc-tach-quyen--dac-ta-8-c-gatekeeper-modules)
 4. [Kiểm Soát Tài Nguyên Toàn Diện: Cgroups v2 & Disk Quota](#4-kiem-soat-tai-nguyen-toan-dien-cgroups-v2--disk-quota)
 5. [Lồng Cách Ly Mức Cao Nhất (Cage Engine & PID 1 Reaper)](#5-long-cach-ly-muc-cao-nhat-cage-engine--pid-1-reaper)
 6. [Module WordPress Manager (Tương đương Plesk WP Toolkit)](#6-module-wordpress-manager-tuong-duong-plesk-wp-toolkit)
 7. [Hệ Thống Template Engine Kế Thừa 3 Cấp](#7-he-thong-template-engine-ke-thua-3-cap)
 8. [App Store Đa Nền Tảng (Debian/Ubuntu & RHEL)](#8-app-store-da-nen-tang-debianubuntu--rhel)
 9. [Thiết Kế Cơ Sở Dữ Liệu Toàn Diện (SQLite Schema Mở Rộng)](#9-thiet-ke-co-so-du-lieu-toan-dien-sqlite-schema-mo-rong)
 10. [Đặc Tả Hệ Thống Full REST API (/api/v1)](#10-dac-ta-he-thong-full-rest-api-apiv1)
 11. [Giao Diện Người Dùng (Plesk Layout & aaPanel Modals)](#11-giao-dien-nguoi-dung-plesk-layout--aapanel-modals)
 12. [Lộ Trình Triển Khai & Chỉ Dẫn Dành Riêng Cho Antigravity](#12-lo-trinh-trien-khai--chi-dan-danh-rieng-cho-antigravity)

---

## 1. Tổng quan Dự án & Triết lý Thiết kế

**PressPanel** là thế hệ Web Control Panel độc lập, hiệu năng cao, siêu nhẹ dành cho máy chủ Linux. Hệ thống kết hợp những ưu điểm xuất sắc nhất của các Control Panel hàng đầu thế giới:

* **Trải nghiệm Quản trị Domain dạng Card của Plesk:** Gom toàn bộ tính năng quản trị một website (PHP, SSL, Database, File Manager, Backup, Cronjob) vào một thẻ trực quan (Websites & Domains view).
* **Kho Ứng dụng & Popup Cấu hình Trực quan của aaPanel:** Cài đặt, gỡ bỏ, bật/tắt PHP Extension chỉ bằng 1-click; sửa file cấu hình trực tiếp qua Monaco Code Editor.
* **WordPress Toolkit Tích hợp Sâu:** Quản lý vòng đời WordPress từ cài đặt 1-click, Clone/Staging, Security Hardening đến đăng nhập tự động 1-click không cần mật khẩu (SSO).
* **Bảo mật Tách Quyền Tuyệt Đối với 8 C Gatekeeper:** Tầng Web API chạy dưới user không có quyền quản trị (`PressPanel:PressPanel`). Mọi tác động xuống Linux Kernel, Cgroups v2, Filesystem Quota và hệ thống file đều do **8 binary C nhỏ gọn (chỉ 100–180 dòng/file, không tiền tố `wrap_`)** thực thi với quyền `root:PressPanel (chmod 4750)`.
* **Backend Python (FastAPI + Asyncio + SQLite WAL):**
  * Tận dụng tối đa khả năng bất đồng bộ (`async`/`await`) cho WebSocket streaming log và task queue.
  * Tự động sinh giao diện tài liệu tương tác **Swagger UI (`/docs`)**, hỗ trợ kiểm thử API và các C Gatekeeper trực tiếp trên trình duyệt.
  * Cú pháp trực quan, dễ bảo trì, dễ tùy biến; RAM tiêu thụ chỉ ~35–45MB khi chạy với Uvicorn.
* **Quản trị Tài nguyên Toàn diện:** Kiểm soát CPU, RAM, IOPS và PIDs thông qua **Cgroups v2 phân cấp Reseller $\rightarrow$ User**, kết hợp **Filesystem Quota (ext4/xfs)** chống tràn ổ cứng.

---

## 2. Cấu trúc Mã Nguồn & Quy hoạch Thư mục Triển khai

### 2.1. Cấu trúc Kho Mã Nguồn Phát Triển (Python FastAPI Repo Layout)

Đây là cấu trúc thư mục phát triển trong mã nguồn của dự án (Git Repo) mà Antigravity / AI Agent sử dụng để sinh code và quản lý:

```
PressPanel/                                # Thư mục gốc Repo
├── Makefile                             # Build C gatekeepers, Frontend Vue 3, setup Python venv
├── install.sh                           # Script cài đặt 1-click lên OS mới
├── requirements.txt                     # Thư viện Python: fastapi, uvicorn[standard], aiosqlite, pydantic, pyjwt, websockets
├── systemd/
│   └── PressPanel.service                 # Systemd Service Unit File
├── gatekeepers/                         # Mã nguồn 8 Binary C SUID
│   ├── Makefile                         # Biên dịch 8 binary C với cờ -O2 -fstack-protector
│   ├── common.h                         # Thư viện dùng chung: sanitize, drop privs, JSON response
│   ├── user.c                           # Quản lý tài khoản Linux & Disk Quota
│   ├── cgroup.c                         # Quản lý Cgroups v2 phân cấp
│   ├── cage.c                           # Lồng cách ly Namespace & PID 1 Reaper
│   ├── vhost.c                          # Cấu hình VirtualHost & SSL
│   ├── db.c                             # Quản lý MariaDB/MySQL
│   ├── app.c                            # Quản lý cài đặt App Store & PHP Extension
│   ├── wp.c                             # Điều khiển WP-CLI trong Cage
│   └── template.c                       # Engine biên dịch Template 3 cấp
├── app/                                 # Backend Python FastAPI
│   ├── main.py                          # FastAPI App, lifespan events, mount Vue 3 StaticFiles
│   ├── core/                            # Cấu hình, bảo mật JWT, password hash, constants
│   │   ├── config.py
│   │   └── security.py
│   ├── db/                              # Kết nối SQLite (WAL mode) & Migrations
│   │   ├── session.py                   # Async connection pool (aiosqlite)
│   │   └── init_db.py                   # Tạo 12 bảng SQLite chuẩn
│   ├── executor/                        # Wrapper gọi 8 binary C gatekeeper an toàn
│   │   └── gatekeeper.py                # Gọi asyncio.create_subprocess_exec, parse JSON output
│   ├── queue/                           # Task Queue bất đồng bộ & WebSocket broadcaster
│   │   └── task_manager.py              # Quản lý chạy ngầm script, ghi file log, stream WS
│   ├── cron/                            # Background Scheduler (SSL renew, metrics sampler, backups)
│   │   └── scheduler.py
│   ├── filemanager/                     # Logic xử lý File Manager an toàn (jail check /home/<user>)
│   │   └── service.py
│   └── api/                             # Router & Handlers REST API
│       ├── deps.py                      # FastAPI Dependencies: get_current_user, check_role (RBAC)
│       └── v1/                          # Endpoints chuẩn RESTful
│           ├── auth.py
│           ├── users.py
│           ├── cgroups.py
│           ├── domains.py
│           ├── wp.py
│           ├── apps.py
│           ├── templates.py
│           ├── files.py
│           ├── crons.py
│           ├── backups.py
│           └── firewall.py
├── web/                                 # Giao diện Frontend (Vue 3 + Vite + TailwindCSS)
│   ├── package.json
│   ├── vite.config.js
│   ├── src/
│   │   ├── api/                         # Axios client gọi REST API FastAPI
│   │   ├── components/                  # DomainCard, MonacoEditor, ModalSetting, TaskConsole
│   │   ├── views/                       # DomainsView, AppStoreView, ResellerView, FileManagerView
│   │   └── stores/                      # Pinia state stores (auth, task, domain)
│   └── dist/                            # Build artifacts (được FastAPI mount phục vụ Web UI)
├── scripts/                             # Bộ bash scripts quản trị triển khai
│   ├── apps/                            # openlitespeed.sh, nginx.sh, php.sh, mariadb.sh...
│   ├── vhost_manager.sh                 # Vhost OLS/Nginx, certbot Let's Encrypt
│   ├── wp_manager.sh                    # Bọc WP-CLI chuyên sâu
│   └── quota_manager.sh                 # Hỗ trợ cấu hình ext4/xfs quota
└── templates/                           # Kho Template cấu hình mặc định (Cấp 1)
    ├── vhost_ols/
    ├── vhost_nginx/
    ├── php_fpm/
    ├── php_ini/
    └── wp_config/
```

### 2.2. Quy hoạch Thư mục Cài đặt Máy chủ (/opt/presspanel)

Khi cài đặt trên máy chủ Linux thực tế, toàn bộ hệ thống được định vị duy nhất trong `/opt/presspanel`:

```
/opt/presspanel/
├── bin/                  # Chứa các binary C SUID (chmod 4750 root:PressPanel)
│   ├── user              # Quản lý tài khoản Linux & Disk Quota
│   ├── cgroup            # Quản lý Cgroups v2 phân cấp
│   ├── cage              # Đóng hộp tiến trình trong Namespace & PID 1 Reaper
│   ├── vhost             # Quản lý VirtualHost OLS/Nginx & SSL
│   ├── db                # Quản lý MariaDB/MySQL
│   ├── app               # Quản lý kho ứng dụng aaPanel
│   ├── wp                # Quản lý WordPress (WP Toolkit)
│   └── template          # Bộ sinh cấu hình Template kế thừa 3 cấp
├── app/                  # Mã nguồn Backend Python FastAPI
│   ├── main.py
│   └── ...
├── venv/                 # Môi trường ảo Python độc lập (Python 3.10+ / 3.11 / 3.12)
│   ├── bin/python
│   └── bin/uvicorn
├── static/               # Thư mục chứa giao diện Vue 3 (web/dist) được FastAPI mount
├── data/                 # Cơ sở dữ liệu SQLite & khóa mã hóa
│   ├── panel.db          # Database chính (chế độ WAL)
│   └── jwt.secret
├── logs/                 # Nhật ký hệ thống & task streaming
│   ├── access.log
│   ├── error.log
│   └── tasks/            # Chứa log chạy ngầm của từng Task ID
├── scripts/              # Bộ script Bash chuẩn hóa do C gatekeeper gọi
│   ├── apps/             # Script cài đặt: openlitespeed.sh, nginx.sh, php.sh, mariadb.sh...
│   ├── vhost_manager.sh  # Tạo/xóa VirtualHost, cấp SSL Let's Encrypt
│   └── wp_manager.sh     # Logic WP-CLI cho WP Toolkit
├── server/               # Đường dẫn chuẩn hóa của các dịch vụ đã cài
│   ├── ols/              # OpenLiteSpeed
│   ├── nginx/            # Nginx server & vhosts
│   ├── php/              # Multi-PHP (7.4, 8.0, 8.1, 8.2, 8.3, 8.4)
│   └── mariadb/          # MariaDB configuration
└── templates/            # Kho lưu trữ template cấu hình 3 cấp
    ├── vhost_ols/        # Template VirtualHost OpenLiteSpeed
    ├── vhost_nginx/      # Template VirtualHost Nginx
    ├── php_fpm/          # Template PHP-FPM Pool
    ├── php_ini/          # Template php.ini
    └── wp_config/        # Template wp-config.php
```

---

## 3. Kiến trúc Tách Quyền & Đặc tả 8 C Gatekeeper Modules

Toàn bộ các module C sử dụng chung thư viện `common.h` để chuẩn hóa kiểm tra bảo mật:

* Xác minh quyền `geteuid() == 0`, nâng `setresuid(0, 0, 0)` và `setresgid(0, 0, 0)` khi cần thao tác đặc quyền.
* Làm sạch biến môi trường bằng `clearenv()`, đặt `PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`.
* Lọc ký tự đầu vào (`is_safe_id` cho username/package_id, `is_safe_token` cho domain/version) chống 100% tấn công Command Injection và Path Traversal.
* Xuất phản hồi ra `stdout`/`stderr` dưới định dạng JSON chuẩn: `{"status":"ok", ...}` hoặc `{"status":"error", "module":"...", "message":"..."}`.

### Cơ chế Gọi từ Python FastAPI (`app/executor/gatekeeper.py`):
```python
import asyncio
import json

async def run_gatekeeper(module: str, *args, stdin_data: str = None) -> dict:
    cmd = [f"/opt/presspanel/bin/{module}"] + list(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin_data else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate(input=stdin_data.encode() if stdin_data else None)
    output = stdout.decode().strip() or stderr.decode().strip()
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"status": "error", "module": module, "message": output}
```

### Bảng tổng hợp Hợp đồng Lệnh (CLI Contract) của 8 Module:

| Tên Binary | Cú pháp CLI (Gọi từ Python FastAPI) | Đầu ra JSON / Tác động hệ thống |
| ----- | ----- | ----- |
| **`user`** | `user create <username> <disk_mb>`<br>`user delete <username>`<br>`user suspend <username>`<br>`user unsuspend <username>`<br>`user set-quota <username> <disk_mb>` | Tạo Linux user, thiết lập Filesystem Quota (ext4/xfs), tạo cấu trúc `/home/<user>/{httpdocs,logs,tmp,ssl,backups}`, chmod 0750; khóa/mở tài khoản bằng `usermod -L/-U`. |
| **`cgroup`** | `cgroup set-reseller <reseller_id> <cpu> <mem> <io> <pids>`<br>`cgroup set-user <reseller_id> <username> <cpu> <mem> <io> <pids>`<br>`cgroup stats <reseller_id> <username>`<br>`cgroup freeze <reseller_id> <username> <0\|1>` | Khởi tạo cây Cgroup v2, kiểm tra chống vượt trần Reseller, đọc RAM/CPU/PIDs thực tế, đóng băng tiến trình User vi phạm. |
| **`cage`** | `cage exec <reseller_id> <username> <cmd> [args...]` | Gắn PID vào Cgroup, tách 4 Namespaces (`CLONE_NEWNS\|NEWPID\|NEWIPC\|NEWUTS`), che `/home` khác bằng tmpfs, mount `/tmp` riêng, chạy PID 1 Reaper dọn dẹp zombie, khóa `PR_SET_NO_NEW_PRIVS`, hạ quyền xuống UID/GID. |
| **`vhost`** | `vhost add <username> <domain> <ols\|nginx> <php_ver>`<br>`vhost remove <username> <domain> <ols\|nginx>`<br>`vhost set-php <username> <domain> <ols\|nginx> <php_ver>`<br>`vhost ssl <username> <domain>` | Gọi script tạo/xóa VirtualHost, đổi phiên bản PHP tức thời và cấp chứng chỉ Let's Encrypt (tự động renew). |
| **`db`** | `db create <db_name> <db_user>` *(mật khẩu qua stdin)*<br>`db drop <db_name> <db_user>`<br>`db backup <username> <db_name> <filename>` | Quản lý database MariaDB qua Unix Socket của root; xuất file `.sql` an toàn vào `/home/<username>/backups/`. |
| **`app`** | `app install <app_name> <version>`<br>`app remove <app_name> <version>`<br>`app status <app_name> <version>`<br>`app php-ext <install\|remove> <php_ver> <ext>`<br>`app service <start\|stop\|restart\|reload> <app_name>` | Tự động nhận diện Debian/Ubuntu (`apt`) hoặc RHEL (`dnf`), thực thi bộ cài trong `/opt/presspanel/scripts/apps/`, bật/tắt extension PHP. |
| **`wp`** | `wp install <username> <domain> <title> <admin_user>` *(pass qua stdin)*<br>`wp clone <username> <src_domain> <dest_domain>`<br>`wp backup <username> <domain> <filename>`<br>`wp restore <username> <domain> <filename>`<br>`wp secure <username> <domain>`<br>`wp plugin <action> <username> <domain> [slug]`<br>`wp theme <action> <username> <domain> [slug]`<br>`wp sso <username> <domain> <admin_user>` | Điều khiển WP-CLI trong Cage: Cài đặt, nhân bản staging, khóa bảo mật, quản lý extension và sinh link đăng nhập 1-click. |
| **`template`** | `template render <app> <pkg_id> <username> <out_path> [K=V ...]`<br>`template set <app> <default\|package\|user> <id>` *(nội dung qua stdin)* | Tìm kiếm template theo thứ tự ưu tiên (User -> Package -> Default), thay thế biến `{{KEY}}` và ghi ra file cấu hình mục tiêu. |

---

## 4. Kiểm Soát Tài Nguyên Toàn Diện: Cgroups v2 & Disk Quota

### 4.1. Cgroups v2 Phân Cấp (Reseller $\rightarrow$ User) & Khóa Trần 2 Lớp

Toàn bộ hệ thống kiểm soát tài nguyên đặt tại `/sys/fs/cgroup/PressPanel`:

```
/sys/fs/cgroup/PressPanel/
├── cgroup.subtree_control           # Bật: +cpu +memory +io +pids
├── reseller_1/                      # Đại lý Reseller 1 (Ví dụ: Trần 4 CPU, 8GB RAM, 500 PIDs)
│   ├── cpu.max                      # 400000 100000
│   ├── memory.max                   # 8589934592
│   ├── pids.max                     # 500
│   ├── cgroup.subtree_control       # Bật: +cpu +memory +io +pids
│   ├── user_clientA/                # User A con (1 CPU, 2GB RAM, 100 PIDs)
│   │   ├── cpu.max                  # 100000 100000
│   │   ├── memory.max               # 2147483648
│   │   ├── pids.max                 # 100
│   │   └── cgroup.procs             # Chứa toàn bộ PID của User Client A
│   └── user_clientB/                # User B con (2 CPU, 4GB RAM, 200 PIDs)
└── reseller_2/
```

**Cơ chế Khóa Trần 2 Lớp:**
1. **Lớp Phần Mềm (`cgroup.c`):** Khi Reseller gọi `cgroup set-user`, module đọc trực tiếp file `memory.max`, `cpu.max`, `pids.max` của thư mục cha `reseller_<id>`. Nếu thông số User lớn hơn thông số Reseller, lệnh bị từ chối ngay lập tức:
   ```json
   {"status":"error","module":"cgroup","message":"RAM User (4096 MB) vuot tran Reseller (2048 MB)"}
   ```
2. **Lớp Nhân Kernel Linux:** Ngay cả khi Reseller tạo nhiều User con mà tổng thông số lý thuyết vượt quá gói (Overselling), Kernel Linux vẫn khóa cứng tổng lượng tiêu thụ tài nguyên thực tế của tất cả các nhánh con không bao giờ vượt qua thư mục cha `reseller_<id>`.

### 4.2. Kiểm Soát Dung Lượng Ổ Đĩa (Disk Quota Enforcement)

Vì Cgroups v2 **không** quản lý dung lượng ổ cứng, PressPanel tích hợp Linux Filesystem Quota qua module `user.c`:

* **Hỗ trợ Hệ thống File:** Tự động phát hiện `ext4` (dùng `setquota` / `quotactl`) hoặc `xfs` (dùng `xfs_quota`).
* **Cơ chế hoạt động:**
  * Khi tạo user: `user create <username> <disk_mb>` $\rightarrow$ gán Hard Limit & Soft Limit cho UID tương ứng.
  * Khi thay đổi gói: `user set-quota <username> <disk_mb>`.
  * Khi User dùng vượt quá hạn mức, hệ thống từ chối ghi file ngay lập tức ở mức Filesystem Kernel (báo lỗi `Disk quota exceeded`), bảo vệ an toàn cho toàn bộ ổ cứng máy chủ.

---

## 5. Lồng Cách Ly Mức Cao Nhất (Cage Engine & PID 1 Reaper)

Khi User thực thi lệnh SSH, Cronjob, WP-CLI hoặc khi tiến trình PHP-FPM / OpenLiteSpeed xử lý request cho một website, lệnh được bọc trong `cage exec <reseller_id> <username> <command>`:

1. **Gán Cgroup:** PID được ghi vào `/sys/fs/cgroup/PressPanel/reseller_<id>/user_<username>/cgroup.procs`.
2. **Tách Namespaces:** `unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWIPC | CLONE_NEWUTS)`.
3. **PID Isolation & PID 1 Reaper (Chống Treo PIDs):**
   * Tiến trình bọc của `cage` trở thành **PID 1** trong PID Namespace mới.
   * `cage.c` cài đặt bộ thu gom tiến trình mồ côi (Subreaper loop với `waitpid(-1, &status, WNOHANG)`) và chuyển tiếp đầy đủ tín hiệu `SIGTERM`, `SIGINT`, `SIGHUP` cho tiến trình con.
   * **Kết quả:** Ngăn chặn hoàn toàn hiện tượng zombie/defunct processes tích tụ làm tràn `pids.max` của Cgroup. Mount lại `/proc` với tùy chọn `hidepid=2`.
4. **Filesystem Isolation (/home Cloaking):**
   * Bind-mount thư mục `/home/<username>` sang một điểm tạm trên RAM.
   * Phủ một phân vùng `tmpfs` trắng lên toàn bộ `/home`.
   * Gắn trả lại duy nhất `/home/<username>` vào `/home`.
   * **Kết quả:** Khi User chạy `ls /home`, danh sách chỉ có duy nhất thư mục của họ.
5. **Private RAM /tmp:** Mount phân vùng `tmpfs` riêng 256MB vào `/tmp` với cờ `MS_NOSUID | MS_NODEV`, ngăn chặn đọc trộm PHP session hoặc tấn công symlink chéo.
6. **Chặn Leo Thang Đặc Quyền:** Gọi `prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)` trước khi hạ quyền sang UID/GID của User bằng `setresgid()` và `setresuid()`.

---

## 6. Module WordPress Manager (Tương đương Plesk WP Toolkit)

Module `wp.c` kết hợp cùng script `/opt/presspanel/scripts/wp_manager.sh` cung cấp đầy đủ chức năng quản trị WordPress chuyên nghiệp:

### 1. Cài đặt WordPress 1-Click (`wp install`)
* Tự động tạo cơ sở dữ liệu và tài khoản MariaDB ngẫu nhiên qua `db create`.
* Tải bản WordPress core mới nhất từ kho chính thức.
* Sinh các khóa Salt bảo mật ngẫu nhiên trong `wp-config.php`.
* Thực thi `wp core install` với các tham số Domain, Title, Admin User, Admin Pass (nhận an toàn qua `stdin`).
* Tự động cài đặt plugin bộ nhớ đệm tương ứng (LSCache cho OpenLiteSpeed hoặc Nginx FastCGI Cache).
* Thiết lập quyền sở hữu file chuẩn xác: `chown -R <user>:<user> /home/<user>/httpdocs`.

### 2. Nhân bản & Môi trường Thử nghiệm (`wp clone` - Staging)
* Sao chép toàn bộ mã nguồn từ `httpdocs` của domain gốc sang domain/subdomain staging.
* Tạo database mới, xuất dump database gốc và import vào database mới.
* Tự động chạy lệnh WP-CLI: `wp search-replace 'https://domain.com' 'https://staging.domain.com' --all-tables` để cập nhật toàn bộ URL trong database mà không làm hỏng dữ liệu serialized.

### 3. Tối ưu Bảo mật (Security Hardening - `wp secure`)
* Bật `define('DISALLOW_FILE_EDIT', true);` trong `wp-config.php` để chặn sửa code từ WP Admin.
* Khóa quyền file nhạy cảm: `chmod 0440 wp-config.php`.
* Tự động xoay vòng bộ khóa Salt (`wp config shuffle-salts`).
* Chặn truy cập file `xmlrpc.php` và thư mục `wp-content/uploads` không cho phép thực thi file `.php`.

### 4. Đăng nhập 1-Click Tự Động (One-Click SSO - `wp sso`)
* Tự động sinh Token đăng nhập một lần (Nonce/One-time login link) qua WP-CLI:
  `wp login create <admin_user> --url-only`
* Trả về đường dẫn đăng nhập trực tiếp vào WP Admin trên giao diện Panel, người dùng không cần nhớ hay nhập lại mật khẩu.

---

## 7. Hệ Thống Template Engine Kế Thừa 3 Cấp

Module `template.c` chịu trách nhiệm sinh toàn bộ file cấu hình của OpenLiteSpeed, Nginx, PHP-FPM, php.ini và MariaDB dựa trên cơ chế kế thừa ưu tiên:

```
Độ ưu tiên cao nhất:   Cấp 3 - User Override  (/opt/presspanel/templates/<app>/user_<username>.tpl)
Độ ưu tiên trung bình: Cấp 2 - Reseller Package (/opt/presspanel/templates/<app>/package_<pkg_id>.tpl)
Độ ưu tiên mặc định:   Cấp 1 - App Default    (/opt/presspanel/templates/<app>/default.tpl)
```

### Danh sách các Placeholder chuẩn hóa:
* `{{DOMAIN}}`: Tên miền của website (ví dụ: `example.com`).
* `{{USERNAME}}`: Linux username sở hữu domain (ví dụ: `client_john`).
* `{{DOCUMENT_ROOT}}`: Đường dẫn thư mục web (ví dụ: `/home/client_john/httpdocs`).
* `{{PHP_VERSION}}`: Phiên bản PHP (ví dụ: `8.2`).
* `{{PHP_SOCK}}`: Đường dẫn Unix socket của PHP-FPM/LSAPI (ví dụ: `/run/php/php8.2-fpm-client_john.sock`).
* `{{MAX_CHILDREN}}`: Số lượng worker tối đa tính toán theo RAM gói hosting.
* `{{MEMORY_LIMIT}}`: Giới hạn RAM cho PHP (ví dụ: `256M`).
* `{{MAX_EXECUTION_TIME}}`: Thời gian thực thi tối đa (ví dụ: `60`).
* `{{SSL_CERT_PATH}}` & `{{SSL_KEY_PATH}}`: Đường dẫn chứng chỉ SSL.

Khi Web API gọi:
```bash
/opt/presspanel/bin/template render vhost_nginx 2 client_john /opt/presspanel/server/nginx/conf/vhost/example.com.conf DOMAIN=example.com USERNAME=client_john PHP_VERSION=8.2
```
Hệ thống sẽ tự động quét xem `user_client_john.tpl` có tồn tại không $\rightarrow$ nếu không, quét `package_2.tpl` $\rightarrow$ nếu không, dùng `default.tpl`, sau đó thay thế toàn bộ thẻ `{{...}}` và ghi ra file đích.

---

## 8. App Store Đa Nền Tảng (Debian/Ubuntu & RHEL)

Module `app.c` tự động đọc `/etc/os-release` và truyền biến môi trường `PANEL_OS_FAMILY` (`debian` hoặc `rhel`) xuống các script trong `/opt/presspanel/scripts/apps/`:

### Bảng Ánh Xạ Package Quản Lý Ứng Dụng:

| Ứng Dụng | Nhánh Debian / Ubuntu (`PANEL_OS_FAMILY=debian`) | Nhánh RHEL / AlmaLinux / Rocky (`PANEL_OS_FAMILY=rhel`) |
| ----- | ----- | ----- |
| **OpenLiteSpeed** | `wget -O - https://repo.litespeed.sh \| bash`<br>`apt-get install -y openlitespeed` | `rpm -Uvh http://rpms.litespeedtech.com/centos/litespeed-repo-1.3-1.el9.noarch.rpm`<br>`dnf install -y openlitespeed` |
| **Nginx** | `apt-get install -y nginx` | `dnf install -y nginx` |
| **MariaDB** | `apt-get install -y mariadb-server mariadb-client` | `dnf install -y MariaDB-server MariaDB-client` |
| **Multi-PHP** | Repo Ondřej Surý: `ppa:ondrej/php`<br>Gói OLS: `apt-get install -y lsphp81 lsphp82 lsphp83 lsphp84` | Repo Remi: `dnf install -y epel-release https://rpms.remirepo.net/...`<br>Gói OLS: `dnf install -y lsphp81 lsphp82 lsphp83 lsphp84` |
| **WP-CLI** | `curl -O https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar`<br>`chmod +x wp-cli.phar && mv wp-cli.phar /usr/local/bin/wp` | Cùng cơ chế binary độc lập |

---

## 9. Thiết Kế Cơ Sở Dữ Liệu Toàn Diện (SQLite Schema Mở Rộng)

Tệp cơ sở dữ liệu đặt tại `/opt/presspanel/data/panel.db`, kích hoạt chế độ WAL (`PRAGMA journal_mode=WAL;`):

```sql
-- 1. Bảng Gói Tài Nguyên (Đã bổ sung disk_mb cho Disk Quota)
CREATE TABLE packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL DEFAULT 0,  -- 0: Admin tạo, >0: Reseller ID tạo
    name TEXT NOT NULL,
    package_type TEXT NOT NULL CHECK(package_type IN ('reseller', 'user')),
    cpu_pct INTEGER NOT NULL,            -- 100 = 1 Core
    mem_mb INTEGER NOT NULL,             -- MB
    disk_mb INTEGER NOT NULL DEFAULT 5120, -- Dung lượng ổ cứng (MB)
    io_weight INTEGER NOT NULL DEFAULT 100,
    pids_max INTEGER NOT NULL DEFAULT 100,
    max_domains INTEGER NOT NULL DEFAULT 10,
    max_databases INTEGER NOT NULL DEFAULT 10,
    max_sub_users INTEGER DEFAULT 0,     -- Áp dụng cho gói Reseller
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Bảng Người Dùng (3 Cấp Phân Quyền RBAC: Admin -> Reseller -> User)
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER DEFAULT NULL,       -- NULL: Admin, ID Reseller nếu là User con
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'reseller', 'user')),
    system_uid INTEGER UNIQUE,           -- Linux UID (>= 1000)
    package_id INTEGER NOT NULL,
    cpu_pct INTEGER NOT NULL,
    mem_mb INTEGER NOT NULL,
    disk_mb INTEGER NOT NULL DEFAULT 5120, -- Hạn mức Disk Quota (MB)
    io_weight INTEGER NOT NULL,
    pids_max INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (package_id) REFERENCES packages(id),
    FOREIGN KEY (parent_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. Bảng Quản Lý Website / Tên Miền
CREATE TABLE domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    domain_name TEXT UNIQUE NOT NULL,
    document_root TEXT NOT NULL,
    webserver TEXT NOT NULL CHECK(webserver IN ('ols', 'nginx')),
    php_version TEXT NOT NULL,           -- '7.4', '8.1', '8.2', '8.3', '8.4'
    ssl_enabled INTEGER DEFAULT 0,
    ssl_expires_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 4. Bảng Quản Lý WordPress Instances (WP Toolkit)
CREATE TABLE wp_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL,
    site_title TEXT NOT NULL,
    admin_user TEXT NOT NULL,
    admin_email TEXT NOT NULL,
    wp_version TEXT NOT NULL,
    db_name TEXT NOT NULL,
    db_user TEXT NOT NULL,
    auto_update_core INTEGER DEFAULT 1,
    auto_update_plugins INTEGER DEFAULT 0,
    is_staging INTEGER DEFAULT 0,
    staging_source_id INTEGER DEFAULT NULL,
    security_score INTEGER DEFAULT 0,    -- Thang điểm 0 - 100
    last_backup_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
);

-- 5. Bảng Cơ Sở Dữ Liệu MariaDB
CREATE TABLE databases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    db_name TEXT UNIQUE NOT NULL,
    db_user TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 6. Bảng Quản Lý Ứng Dụng Đã Cài (aaPanel App Store)
CREATE TABLE installed_apps (
    app_name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('installing', 'active', 'failed', 'removing')),
    installed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (app_name, version)
);

-- 7. Bảng Hàng Đợi Tác Vụ Bất Đồng Bộ (Task Queue & Log Streaming)
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,                 -- UUID
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,                -- Ví dụ: 'app:install:ols', 'wp:clone', 'backup:full'
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    log_file TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

-- 8. Bảng API Keys
CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    key_name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    scopes TEXT NOT NULL,                -- 'all', 'domains:read', 'cgroups:write', 'files:write'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 9. Bảng Quản Lý Cronjob của User (Thực thi trong Cage)
CREATE TABLE cron_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    schedule TEXT NOT NULL,              -- Cron syntax: '*/15 * * * *'
    command TEXT NOT NULL,               -- Lệnh thực thi: 'wp cron event run --due-now'
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 10. Bảng Chi Tiết Chứng Chỉ SSL
CREATE TABLE ssl_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL,
    provider TEXT NOT NULL CHECK(provider IN ('letsencrypt', 'custom', 'self_signed')),
    cert_path TEXT NOT NULL,
    key_path TEXT NOT NULL,
    auto_renew INTEGER DEFAULT 1,
    expires_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
);

-- 11. Bảng Quản Lý Bản Sao Lưu Toàn Diện (Backups)
CREATE TABLE backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    domain_id INTEGER DEFAULT NULL,       -- NULL: Backup toàn bộ user
    backup_type TEXT NOT NULL CHECK(backup_type IN ('full', 'files', 'db')),
    storage_type TEXT NOT NULL DEFAULT 'local' CHECK(storage_type IN ('local', 's3', 'ftp')),
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 12. Bảng Luật Tường Lửa & Chặn IP (Firewall Rules)
CREATE TABLE firewall_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    port INTEGER,
    protocol TEXT NOT NULL DEFAULT 'tcp' CHECK(protocol IN ('tcp', 'udp')),
    action TEXT NOT NULL CHECK(action IN ('allow', 'deny')),
    ip_source TEXT DEFAULT '0.0.0.0/0',
    comment TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 10. Đặc Tả Hệ Thống Full REST API (/api/v1)

Tất cả các endpoint yêu cầu Header `Authorization: Bearer <JWT_OR_API_KEY>`. Khi truy cập `/docs`, FastAPI tự động hiển thị tài liệu Swagger UI đầy đủ và cho phép gọi thử API trực tiếp.

### 1. Phân hệ Xác thực & Tài khoản (`/api/v1/auth`, `/api/v1/users`)
* `POST /api/v1/auth/login`: Đăng nhập, nhận JWT Token chứa `user_id`, `role`, `parent_id`.
* `POST /api/v1/users`: Tạo User mới $\rightarrow$ API gọi binary `user create <username> <disk_mb>` và `cgroup set-user`.
* `DELETE /api/v1/users/{id}`: Xóa tài khoản $\rightarrow$ API gọi binary `user delete <username>` và dọn dẹp cgroup.
* `POST /api/v1/users/{id}/suspend`: Khóa tài khoản $\rightarrow$ API gọi binary `user suspend <username>` và `cgroup freeze ... 1`.
* `POST /api/v1/users/{id}/unsuspend`: Mở khóa $\rightarrow$ API gọi binary `user unsuspend <username>` và `cgroup freeze ... 0`.
* `PUT /api/v1/users/{id}/quota`: Điều chỉnh Disk Quota $\rightarrow$ API gọi binary `user set-quota <username> <disk_mb>`.

### 2. Phân hệ Cgroups v2 & Giám sát (`/api/v1/cgroups`)
* `PUT /api/v1/resellers/{id}/cgroup`: Đặt hạn mức gói Reseller $\rightarrow$ API gọi `cgroup set-reseller`.
* `PUT /api/v1/users/{id}/cgroup`: Cập nhật hạn mức User $\rightarrow$ API gọi `cgroup set-user` (tự động chặn vượt trần Reseller).
* `GET /api/v1/users/{id}/stats`: Trả về dữ liệu tiêu thụ CPU, RAM (bytes), số lượng PIDs thời gian thực từ binary `cgroup stats`.

### 3. Phân hệ Domain & Web Server (`/api/v1/domains`)
* `GET /api/v1/domains`: Danh sách domain của User (hoặc tất cả domain thuộc Reseller đang quản lý).
* `POST /api/v1/domains`: Thêm website mới $\rightarrow$ API gọi `vhost add <user> <domain> <ols|nginx> <php_ver>`.
* `PUT /api/v1/domains/{id}/php`: Chuyển đổi phiên bản PHP $\rightarrow$ API gọi `vhost set-php`.
* `POST /api/v1/domains/{id}/ssl`: Cấp SSL Let's Encrypt $\rightarrow$ API gọi `vhost ssl`.
* `DELETE /api/v1/domains/{id}`: Xóa tên miền $\rightarrow$ API gọi `vhost remove`.

### 4. Phân hệ WordPress Manager (`/api/v1/wp`)
* `POST /api/v1/wp/install`: Cài đặt WordPress 1-Click $\rightarrow$ API gọi `wp install`.
* `POST /api/v1/wp/{id}/clone`: Clone website sang domain/subdomain staging $\rightarrow$ API gọi `wp clone`.
* `POST /api/v1/wp/{id}/secure`: Kích hoạt bộ bảo mật Security Hardening $\rightarrow$ API gọi `wp secure`.
* `GET /api/v1/wp/{id}/sso`: Tạo đường dẫn đăng nhập 1-click tự động $\rightarrow$ API gọi `wp sso`.
* `GET /api/v1/wp/{id}/plugins`: Lấy danh sách plugin đã cài $\rightarrow$ API gọi `wp plugin list`.
* `POST /api/v1/wp/{id}/plugins/update`: Cập nhật plugin $\rightarrow$ API gọi `wp plugin update`.

### 5. Phân hệ App Store (`/api/v1/apps`)
* `GET /api/v1/apps`: Danh sách ứng dụng có sẵn trong kho, phiên bản và trạng thái cài đặt.
* `POST /api/v1/apps/{name}/install`: Cài đặt ứng dụng $\rightarrow$ Tạo Task bất đồng bộ, gọi `app install <name> <version>`.
* `POST /api/v1/apps/php-ext`: Bật/tắt PHP Extension $\rightarrow$ Gọi `app php-ext <install|remove> <php_ver> <ext>`.
* `POST /api/v1/apps/{name}/service`: Điều khiển dịch vụ $\rightarrow$ Gọi `app service <start|stop|restart|reload> <name>`.
* `GET /api/v1/tasks/{id}/stream`: WebSocket truyền log cài đặt trực tiếp lên trình duyệt.

### 6. Phân hệ Template Engine (`/api/v1/templates`)
* `GET /api/v1/templates/{app}`: Danh sách các template cấu hình có sẵn của ứng dụng đó.
* `POST /api/v1/templates/{app}`: Lưu template tùy biến cho Gói hoặc User $\rightarrow$ Gọi `template set`.

### 7. Phân hệ Quản Lý Tệp Tin - File Manager (`/api/v1/files`)
* `GET /api/v1/files/list?path=...`: Liệt kê tệp tin và thư mục (tên, kích thước, quyền, sửa đổi lần cuối).
* `POST /api/v1/files/upload`: Tải tệp tin lên (hỗ trợ multipart stream, kiểm tra chống ghi đè trái phép).
* `GET /api/v1/files/download?path=...`: Tải tệp tin về máy client.
* `GET /api/v1/files/content?path=...`: Đọc nội dung tệp văn bản (để mở trong Monaco Editor).
* `PUT /api/v1/files/content`: Lưu nội dung tệp văn bản sau khi chỉnh sửa.
* `POST /api/v1/files/archive`: Nén danh sách tệp/thư mục thành `.zip` hoặc `.tar.gz`.
* `POST /api/v1/files/unarchive`: Giải nén file nén tại thư mục đích.
* `DELETE /api/v1/files`: Xóa tệp tin hoặc thư mục.
* *Nguyên tắc an toàn:* Toàn bộ đường dẫn bắt buộc phải được chuẩn hóa qua `os.path.realpath()` và xác thực nằm hoàn toàn bên trong `/home/<username>/`.

### 8. Phân hệ Tác Vụ Định Kỳ - Cron Manager (`/api/v1/crons`)
* `GET /api/v1/crons`: Lấy danh sách cronjob của user hiện tại.
* `POST /api/v1/crons`: Tạo mới cronjob $\rightarrow$ Ghi vào DB và cập nhật crontab của Linux User (chạy bọc qua `cage`).
* `PUT /api/v1/crons/{id}`: Chỉnh sửa cú pháp lịch trình hoặc câu lệnh.
* `DELETE /api/v1/crons/{id}`: Xóa cronjob.

### 9. Phân hệ Sao Lưu & Phục Hồi - Backup Manager (`/api/v1/backups`)
* `GET /api/v1/backups`: Lấy danh sách các bản sao lưu.
* `POST /api/v1/backups/create`: Tạo bản sao lưu mới (`full`, `files`, hoặc `db`) $\rightarrow$ Đẩy vào Task Queue bất đồng bộ.
* `POST /api/v1/backups/{id}/restore`: Phục hồi website/database từ bản sao lưu.
* `DELETE /api/v1/backups/{id}`: Xóa file sao lưu.
* `POST /api/v1/backups/{id}/upload-remote`: Đẩy bản sao lưu lên kho lưu trữ từ xa (S3 / SFTP).

### 10. Phân hệ Tường Lửa & Chống Xâm Nhập - Firewall (`/api/v1/firewall`)
* `GET /api/v1/firewall/rules`: Xem danh sách các port đang mở và IP đang bị chặn.
* `POST /api/v1/firewall/rules`: Thêm quy tắc mở port hoặc chặn IP (gọi `ufw` hoặc `firewall-cmd`).
* `DELETE /api/v1/firewall/rules/{id}`: Xóa quy tắc tường lửa.
* `GET /api/v1/firewall/fail2ban/status`: Xem trạng thái các jail chống brute-force (SSH, Panel Login, WP Login).

---

## 11. Giao Diện Người Dùng (Plesk Layout & aaPanel Modals)

Giao diện SPA xây dựng bằng **Vue 3 + Vite + TailwindCSS**, tổ chức theo các màn hình trung tâm:

### 1. Màn hình "Websites & Domains" (Phong cách Plesk)
* Hiển thị mỗi website dưới dạng một Thẻ (Card lớn) có thể mở rộng.
* **Header Thẻ:** Tên miền, IP, Trạng thái SSL (Xanh: Còn hạn kèm ngày hết hạn, Đỏ: Lỗi/Chưa có), Nút truy cập web.
* **Thân Thẻ (3 Cột Tiện Ích):**
  * *Cột 1 (Dev & Files):* Nút mở File Manager, Trình chọn phiên bản PHP (dropdown 7.4 - 8.4), FTP, Git Deploy, Nút xem Log truy cập/lỗi.
  * *Cột 2 (WordPress Toolkit Card):* Logo WordPress, phiên bản hiện tại, Trạng thái cập nhật, Nút **"Log in to WP-Admin" (1-Click SSO)**, Nút **"Clone to Staging"**, Nút **"Check Security"**.
  * *Cột 3 (Tài nguyên & Cơ sở dữ liệu):* MariaDB Database (nút mở phpMyAdmin tự động login), Biểu đồ mini hiển thị % RAM / % CPU thời gian thực từ Cgroups v2 và thanh đo dung lượng ổ cứng (Disk Quota).

### 2. Màn hình "File Manager" (Trực quan & Hiện đại)
* Thanh điều hướng cây thư mục trực quan (Breadcrumb).
* Hỗ trợ kéo thả để tải lên tệp tin (Drag & Drop upload).
* Tích hợp **Monaco Editor** với cú pháp tô màu syntax highlighting đầy đủ cho PHP, HTML, CSS, JS, Nginx/Apache config.
* Hỗ trợ thao tác nén, giải nén ZIP trực tiếp trên giao diện web chỉ với 1 click.

### 3. Màn hình "App Store" (Phong cách aaPanel)
* Danh sách ứng dụng chia theo thẻ danh mục: *Web Server (OLS, Nginx)*, *Database (MariaDB, Redis, Memcached)*, *PHP Runtime (PHP 7.4 đến 8.4)*, *Công cụ hệ thống (Pure-FTPd, PowerDNS, phpMyAdmin)*.
* Trạng thái cài đặt: Nút **"Install"** (mở modal chọn bản Fast/Precompiled), hoặc Nút **"Setting"** khi đã cài.
* **Modal Setting (Đa Tab):**
  * *Tab 1 - Service:* Bật, Tắt, Khởi động lại, Tải lại dịch vụ kèm trạng thái tiến trình (Active/Running, PID, Memory).
  * *Tab 2 - Configuration:* Trình biên tập mã **Monaco Editor** cho phép sửa file `nginx.conf`, `httpd_config.conf` hoặc `php.ini` trực tiếp với cú pháp tô màu.
  * *Tab 3 - PHP Extensions (Đối với PHP):* Bảng danh sách extension (`redis`, `opcache`, `imagick`, `ioncube`...), gạt công tắc Switch để cài đặt hoặc gỡ bỏ trong 1 click.
  * *Tab 4 - Logs:* Xem file error log và access log theo thời gian thực.

### 4. Màn hình "Reseller & Packages" (Quản lý Đại lý & Gói Cước)
* Giao diện phân bổ tài nguyên: Thanh trượt (Slider) giới hạn CPU Quota, Memory Limit, Disk Quota, Số domain tối đa.
* Dashboard giám sát tổng tài nguyên: Reseller nhìn thấy biểu đồ tổng mức sử dụng thực tế của tất cả User con so với trần hạn mức của gói Reseller.

---

## 12. Lộ Trình Triển Khai & Chỉ Dẫn Dành Riêng Cho Antigravity

Khi đưa tài liệu này vào Antigravity / AI Code Agent, hãy thực hiện theo đúng 5 bước tuần tự sau:

### Bước 1: Khởi tạo Cấu trúc Repo & Biên dịch 8 Module C Gatekeeper
1. Tạo thư mục `gatekeepers/` chứa toàn bộ mã nguồn C (`common.h`, `user.c`, `cgroup.c`, `cage.c`, `vhost.c`, `db.c`, `app.c`, `wp.c`, `template.c`, `Makefile`).
2. Biên dịch với cờ an toàn:
   ```bash
   cd gatekeepers && make all
   ```
3. Cài đặt vào hệ thống:
   ```bash
   sudo make install
   ```
4. Xác nhận toàn bộ 8 file thực thi đã nằm tại `/opt/presspanel/bin/` và có cờ quyền `4750 root:PressPanel`.

### Bước 2: Khởi tạo Cấu trúc Thư mục Hệ thống & Database SQLite
1. Chạy script tạo đầy đủ cây thư mục triển khai:
   ```bash
   mkdir -p /opt/presspanel/{bin,app,static,data,logs/tasks,scripts/apps,server,templates}
   ```
2. Tạo database SQLite tại `/opt/presspanel/data/panel.db` và nạp toàn bộ cấu trúc bảng từ Mục 9 (12 bảng) với `PRAGMA journal_mode=WAL;`.

### Bước 3: Phát triển Backend API Server bằng Python (FastAPI + Asyncio)
1. Khởi tạo môi trường ảo Python độc lập tại `/opt/presspanel/venv/`:
   ```bash
   python3 -m venv /opt/presspanel/venv
   /opt/presspanel/venv/bin/pip install -r requirements.txt
   ```
2. Tạo module `app/executor/gatekeeper.py`: Viết hàm thực thi gọi 8 binary C tại `/opt/presspanel/bin/<module>` bằng `asyncio.create_subprocess_exec()`, đọc phản hồi JSON chuẩn từ `stdout`/`stderr`.
3. Xây dựng Middleware RBAC & Dependencies (`app/api/deps.py`): Kiểm tra JWT Token, xác minh quan hệ `parent_id` (đảm bảo Reseller chỉ có quyền tác động vào User con do mình quản lý).
4. Xây dựng Task Queue: Chạy các tác vụ tốn thời gian (cài app, clone WP, backup) bằng `asyncio.create_task()`, ghi log ra `/opt/presspanel/logs/tasks/<task_id>.log` và truyền qua WebSocket endpoint `/api/v1/tasks/{id}/stream`.
5. Xây dựng File Manager Service (`app/filemanager/`): Kiểm tra chroot jail `/home/<user>` và xử lý thao tác file an toàn.
6. Kiểm thử API tức thì qua Swagger UI tại `http://localhost:8888/docs`.

### Bước 4: Viết Bộ Script Bash Hậu Cần (`/opt/presspanel/scripts/`)
1. Viết `vhost_manager.sh`: Xử lý tạo vhost OLS/Nginx, tạo symlink và gọi Certbot cấp SSL.
2. Viết `wp_manager.sh`: Bọc các lệnh WP-CLI (`wp core install`, `wp search-replace`, `wp plugin`, `wp login create`).
3. Viết các bộ cài trong `/opt/presspanel/scripts/apps/` (`openlitespeed.sh`, `nginx.sh`, `php.sh`, `mariadb.sh`).

### Bước 5: Xây dựng Giao diện Frontend Vue 3 & Cấu hình Phục vụ Hệ Thống
1. Dựng ứng dụng Vue 3 với Vite, TailwindCSS, Lucide Icons, Monaco Editor và Pinia trong thư mục `web/`.
2. Xây dựng các màn hình cốt lõi: Websites & Domains (Plesk), App Store (aaPanel), File Manager, Backup & Cron, Reseller Management.
3. Build frontend ra thư mục `web/dist/` và copy vào `/opt/presspanel/static/`. Trong FastAPI `main.py`, mount phục vụ Web UI:
   ```python
   from fastapi.staticfiles import StaticFiles
   app.mount("/", StaticFiles(directory="/opt/presspanel/static", html=True), name="static")
   ```
4. Cấu hình Systemd Unit File `PressPanel.service` khởi chạy Uvicorn:
   ```ini
   [Unit]
   Description=PressPanel Control Panel Server
   After=network.target

   [Service]
   Type=simple
   User=PressPanel
   Group=PressPanel
   WorkingDirectory=/opt/presspanel
   ExecStart=/opt/presspanel/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8888 --workers 2
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
5. **Kết quả cuối cùng:** Hệ thống Control Panel vận hành với backend **FastAPI Asyncio** siêu mượt mà, trực quan, dễ mở rộng, kết hợp cùng **bộ 8 công cụ C `bin/*`**, sẵn sàng đưa vào vận hành thực tế!

