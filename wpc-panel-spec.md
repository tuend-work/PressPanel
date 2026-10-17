# WPCloud (WPC Control Panel): Master Architecture & Technical Specification

> **Tài liệu đặc tả kiến trúc kỹ thuật toàn diện (Master Architectural Blueprint & Context Specification)** được biên soạn chuẩn hóa để cung cấp ngữ cảnh đầy đủ 100% cho các hệ thống AI (Antigravity, Claude, ChatGPT, DeepSeek, v.v.) và lập trình viên khi tiếp cận, phát triển hoặc bảo trì dự án WPC.

---

## MỤC LỤC
1. [Tổng Quan Dự Án & Ngữ Cảnh Tiến Hóa](#1-tổng-quan-dự-án--ngữ-cảnh-tiến-hóa)
2. [Mô Hình Phân Quyền & Quản Lý Danh Tính 3 Cấp (RBAC)](#2-mô-hình-phân-quyền--quản-lý-danh-tính-3-cấp-rbac)
3. [Hệ Thống Hai Tầng Gói Dịch Vụ (Dual-Tier Service Plans)](#3-hệ-thống-hai-tầng-gói-dịch-vụ-dual-tier-service-plans)
4. [Quy Hoạch Thư Mục Hệ Thống & Cấu Trúc Mã Nguồn](#4-quy-hoạch-thư-mục-hệ-thống--cấu-trúc-mã-nguồn)
5. [Kiến Trúc Tách Quyền: 8 Binary C SUID Gatekeepers](#5-kiến-trúc-tách-quyền-8-binary-c-suid-gatekeepers)
6. [Kiểm Soát Tài Nguyên: Cgroups v2 Phân Cấp & Linux Disk Quota](#6-kiểm-soát-tài-nguyên-cgroups-v2-phân-cấp--linux-disk-quota)
7. [Lồng Cách Ly Bảo Mật: Cage Engine & PID 1 Reaper](#7-lồng-cách-ly-bảo-mật-cage-engine--pid-1-reaper)
8. [Kiến Trúc Web Server & Đa Phiên Bản PHP-FPM](#8-kiến-trúc-web-server--đa-phiên-bản-php-fpm)
9. [Module WordPress Toolkit Toàn Diện](#9-module-wordpress-toolkit-toàn-diện)
10. [Lược Đồ Cơ Sở Dữ Liệu SQLite (12 Bảng Chuẩn)](#10-lược-đồ-cơ-sở-dữ-liệu-sqlite-12-bảng-chuẩn)
11. [Đặc Tả Toàn Diện Hệ Thống REST API (/api/v1) & WebSocket](#11-đặc-tả-toàn-diện-hệ-thống-rest-api-apiv1--websocket)
12. [Kiến Trúc Giao Diện Frontend (Vue 3 Single Page Application)](#12-kiến-trúc-giao-diện-frontend-vue-3-single-page-application)
13. [Bộ Lệnh Quản Trị Hệ Thống (`wpc` CLI) & Vận Hành](#13-bộ-lệnh-quản-trị-hệ-thống-wpc-cli--vận-hành)

---

## 1. Tổng Quan Dự Án & Ngữ Cảnh Tiến Hóa

### 1.1. Tên gọi & Định vị
* **Tên thương mại & Sản phẩm:** **WPCloud (WPC Control Panel)**.
* **Mã nội bộ ban đầu (Legacy Codebase):** `PressPanel` (trong quá trình phát triển, toàn bộ logic đã được quy hoạch chuẩn sang nhận diện thương hiệu `wpc`, thư mục `/opt/wpc`, file cấu hình `/opt/wpc/conf/wpc.conf`, service `wpc.service`).
* **Mục tiêu:** Bảng điều khiển máy chủ Linux độc lập, siêu nhẹ (~35MB RAM), khởi động tức thì, tối ưu chuyên sâu cho WordPress và website PHP hiệu năng cao, thay thế hoàn toàn các panel cồng kềnh như cPanel, DirectAdmin, CyberPanel, aaPanel, Plesk.

### 1.2. Công nghệ Cốt lõi
* **Backend:** Python 3 (FastAPI + Asyncio + Uvicorn) kết hợp SQLite nhúng chế độ WAL (`/opt/wpc/data/panel.db`).
* **Kernel & Bảo mật:** 8 Binary C SUID (chmod 4750 root:wpc), Cgroups v2 phân cấp, Linux Namespace Cage, Linux Filesystem Quota.
* **Web Server:** OpenLiteSpeed (LSCache) hoặc Nginx + Apache (Reverse Proxy).
* **PHP Engine:** Đa phiên bản song song từ PHP 5.6 đến PHP 8.5 với PHP-FPM Pool cô lập riêng cho từng VirtualHost.
* **Frontend:** Vue 3 Composition API Single Page Application (SPA), Tailwind CSS, Lucide Icons, Monaco Code Editor, xterm.js (Web Terminal).

---

## 2. Mô Hình Phân Quyền & Quản Lý Danh Tính 3 Cấp (RBAC)

Hệ thống WPC phân tách rõ rệt 3 cấp độ người dùng để bảo đảm an toàn dữ liệu và tối ưu trải nghiệm:

```text
┌─────────────────────────────────────────────────────────────────┐
│                    LEVEL 1: ADMINISTRATOR                       │
│  - Quản trị toàn bộ máy chủ vật lý, cấu hình hệ thống wpc.conf  │
│  - Quản lý danh sách Users (Khách hàng) & User Packages         │
│  - Quản lý Cgroups hệ thống, App Store, PHP Manager, Firewall   │
└───────────────────────────────┬─────────────────────────────────┘
                                │ cấp User Package (Hạn mức tổng)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                  LEVEL 2: USER (HOSTING USER)                   │
│  - Khách hàng mua/sở hữu tài khoản hosting (ví dụ: dtnguyen)    │
│  - Quản lý nhiều website trong hạn mức tổng của User Package    │
│  - Dashboard riêng biệt: 4 đồng hồ đo CPU/RAM/Disk/Container    │
│  - Tự tạo và quản lý các Website Plans (mỗi gói cho 1 website)  │
│  - Quản lý Domains, Databases, Mail, Cronjobs, Backups          │
└───────────────────────────────┬─────────────────────────────────┘
                                │ tạo và cách ly từng website
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│               LEVEL 3: WEB USER (ISOLATED USER)                 │
│  - Người dùng Linux cô lập phụ trách 1 website cụ thể           │
│  - Chạy PHP-FPM Pool riêng biệt với UID/GID tương ứng           │
│  - Document root: /home/{user}/domains/{domain}/public_html     │
│  - File Manager & Cage SSH Shell hoàn toàn cách ly              │
└─────────────────────────────────────────────────────────────────┘
```

### Chế độ Đăng nhập Chuyển đổi (Impersonation Mode)
1. **Login as User:** Administrator có thể đăng nhập vào tài khoản của bất kỳ Hosting User nào từ menu `Users` để hỗ trợ kỹ thuật mà không cần biết mật khẩu. Giao diện xuất hiện banner cam thông báo: `Đang đăng nhập dưới quyền User: {username} (Chế độ Login as User)`.
2. **Login as Web User:** Administrator hoặc Hosting User có thể đăng nhập vào phiên quản trị cô lập của một website cụ thể từ thẻ website trong `Websites & Domains`. Khi đó giao diện chuyển sang phiên làm việc của Web User (File Manager của website đó, Web Terminal trong Cage của website đó).

---

## 3. Hệ Thống Hai Tầng Gói Dịch Vụ (Dual-Tier Service Plans)

Hệ thống phân tách rạch ròi 2 loại gói dịch vụ nhằm phân bổ tài nguyên chặt chẽ:

| Tiêu chí | Loại 1: User Package (User Plan) | Loại 2: Website Plan |
| :--- | :--- | :--- |
| **Đối tượng áp dụng** | Toàn bộ tài khoản User (Hosting User) | Từng VirtualHost / Website đơn lẻ |
| **Người tạo & Quản lý**| **Chỉ Administrator** | **User (Hosting User)** hoặc Administrator |
| **Quy mô số Website** | `max_domains` từ 1 đến không giới hạn | **Bắt buộc 1 Website duy nhất** (`max_domains = 1`) |
| **Hạn mức tài nguyên** | Cấp trần tài nguyên máy chủ vật lý | Nằm trong hạn mức gói User Package của tài khoản |
| **Vị trí hiển thị trên UI** | Sidebar Admin: **`User Package`** | Sidebar Hosting User: **`Website Plan`** |
| **Quy tắc hiển thị** | **Chỉ Admin mới thấy User Plan** | **Hosting User chỉ nhìn thấy Website Plan** |

> [!IMPORTANT]
> **Quy tắc Nghiệp Vụ Cốt Lõi:**
> - Khi Hosting User truy cập trang quản lý gói, API `/api/v1/users/plans` lọc bắt buộc `plan_type = 'website'`. Hosting User tuyệt đối không bao giờ nhìn thấy các gói User Plan của Admin.
> - Mỗi Website Plan đại diện cho cấu hình tài nguyên của **đúng 1 website** (CPU %, RAM MB, Disk MB, Database limit, SSH toggle).

---

## 4. Quy Hoạch Thư Mục Hệ Thống & Cấu Trúc Mã Nguồn

### 4.1. Cấu Trúc Repo Phát Triển Git (`PressPanel/`)
```text
PressPanel/
├── VERSION                     # File chứa chuỗi version hiện tại (VD: 0.5.17)
├── Makefile                    # Biên dịch gatekeepers & thiết lập môi trường
├── install.sh                  # Bộ cài đặt tự động 1-click lên OS mới
├── requirements.txt            # Thư viện Python: fastapi, uvicorn, aiosqlite, pyjwt...
├── systemd/
│   └── wpc.service             # Systemd Unit File chính thức
├── gatekeepers/                # 8 Mã nguồn C SUID Gatekeepers
│   ├── Makefile                # Build với cờ -O2 -fstack-protector-strong -fPIE
│   ├── common.h                # Header dùng chung (drop privs, sanitize, JSON)
│   ├── user.c                  # Quản lý tài khoản Linux & Disk Quota
│   ├── cgroup.c                # Quản lý Cgroups v2 phân cấp
│   ├── cage.c                  # Lồng cách ly Namespace & PID 1 Reaper
│   ├── vhost.c                 # Cấu hình VirtualHost & SSL Certbot
│   ├── db.c                    # Quản trị MariaDB / MySQL
│   ├── app.c                   # Cài đặt App Store & PHP Extension
│   ├── wp.c                    # Wrapper gọi WP-CLI an toàn trong Cage
│   └── template.c              # Engine kế thừa mẫu cấu hình 3 cấp
├── app/                        # FastAPI Backend Server
│   ├── main.py                 # Khởi tạo FastAPI App & mount router/static
│   ├── core/                   # Cấu hình wpc.conf, JWT, Password Hashing
│   │   ├── config.py           # Parse key=value từ /opt/wpc/conf/wpc.conf
│   │   └── security.py         # JWT Token & bcrypt verification
│   ├── db/                     # Quản lý kết nối SQLite WAL & Migrations
│   │   ├── session.py          # aiosqlite connection provider
│   │   └── init_db.py          # Tạo 12 bảng SQLite & Migration tự động
│   ├── executor/               # Gọi 8 C gatekeeper qua asyncio.create_subprocess_exec
│   │   └── gatekeeper.py
│   ├── queue/                  # Task Manager ngầm & WebSocket broadcast
│   │   └── task_manager.py
│   ├── cron/                   # Bộ lập lịch chạy nền (Auto-SSL, metrics, backups)
│   │   └── scheduler.py
│   ├── filemanager/            # Xử lý tệp an toàn (Jail check trong document root)
│   │   └── service.py
│   └── api/                    # Tầng REST API Routers (/api/v1)
│       ├── deps.py             # Dependency get_current_user, require_role
│       └── v1/
│           ├── auth.py         # Login, JWT, impersonate, upgrade user
│           ├── users.py        # Users, Customers, User Packages, Website Plans
│           ├── domains.py      # VirtualHosts, SSL, DNS, PHP version switch
│           ├── wp.py           # WordPress 1-Click, SSO, Clone, Hardening
│           ├── apps.py         # App Store, Service status, PHP Extensions
│           ├── files.py        # File Manager (Read, Write, Zip, Unzip, Chmod)
│           ├── databases.py    # MySQL databases, DB users, phpMyAdmin
│           ├── crons.py        # Quản lý Cronjobs theo user
│           ├── backups.py      # Backup Full, Files, Database (Local, S3)
│           ├── firewall.py     # Cấu hình UFW / Iptables
│           └── system.py       # Hardware metrics, Cgroups usage, WebSocket logs
├── static/                     # Giao diện Frontend Vue 3 SPA
│   ├── index.html              # Shell chính: Sidebar, Topbar, Vue App State
│   ├── views/                  # Tách nhỏ các view HTML (Dynamic Component Loader)
│   │   ├── dashboard.html      # Tổng quan (4 đồng hồ đo + Website Plans overview)
│   │   ├── domains.html        # Danh sách Website dạng Obsidian Card
│   │   ├── service_plans.html  # Quản lý User Packages / Website Plans
│   │   ├── customers.html      # Quản lý Users (Khách hàng)
│   │   ├── databases.html      # MySQL & phpMyAdmin
│   │   ├── wp.html             # WordPress Toolkit
│   │   ├── apps.html           # App Store 1-Click
│   │   ├── files.html          # Monaco File Manager
│   │   ├── mail.html           # Mail Server & Webmail
│   │   └── others.html         # Settings, Monitoring, Backups, Firewall
│   └── modals/                 # Toàn bộ Popup Modal (Add Domain, Add Plan...)
│       └── modals.html
└── scripts/                    # Các shell script hỗ trợ quản trị
    ├── wpc                     # Lệnh CLI chính thức (/usr/local/bin/wpc)
    ├── update.sh               # Kịch bản cập nhật từ GitHub
    ├── reinstall.sh            # Kịch bản cài đặt lại bảo toàn dữ liệu
    ├── cgroup_attach.sh        # Gắn tiến trình FPM vào Cgroups
    └── apps/                   # Script cài đặt tự động từng ứng dụng
```

### 4.2. Cấu Trúc Triển Khai Runtime Trên Máy Chủ Linux (`/opt/wpc/`)
```text
/opt/wpc/
├── bin/                        # 8 C SUID Gatekeepers (chmod 4750 root:wpc)
├── app/                        # FastAPI Backend
├── conf/
│   └── wpc.conf                # File cấu hình trung tâm (key=value)
├── venv/                       # Python Virtualenv độc lập
├── static/                     # Web UI Single Page Application
├── data/
│   └── panel.db                # SQLite 3 Database (WAL mode)
├── logs/
│   ├── access.log              # API Access log
│   ├── error.log               # Error exception log
│   └── tasks/                  # Task execution logs (stream qua WebSocket)
├── scripts/                    # Quản trị viên scripts
├── server/                     # Runtime dịch vụ máy chủ
│   ├── openlitespeed/ hoặc nginx/
│   ├── php/                    # Các phiên bản PHP (5.6 - 8.5)
│   └── mariadb/
└── templates/                  # Template vhost kế thừa 3 cấp
```

### 4.3. Cấu Trúc Document Root Chuẩn Cho Mỗi Website
```text
/home/{username}/domains/{domain.com}/
├── public_html/                # Mã nguồn chính của website (document root)
├── logs/
│   ├── access.log              # Access log riêng của domain
│   └── error.log               # Error log riêng của domain
├── ssl/
│   ├── cert.pem                # Chứng chỉ SSL Let's Encrypt / Custom
│   └── key.pem                 # Khóa bí mật SSL
└── .tmp/                       # Thư mục tạm upload & session riêng của domain
```
*Tự động tạo symlink `/home/{username}/public_html` trỏ tới website chính đầu tiên.*

---

## 5. Kiến Trúc Tách Quyền: 8 Binary C SUID Gatekeepers

### 5.1. Triết Lý Bảo Mật
- Web API Server chạy hoàn toàn dưới user không đặc quyền: **`wpc:wpc`**.
- Tuyệt đối **không chạy backend Python dưới quyền `root`**.
- Mọi thao tác cần đặc quyền hệ thống (chạm kernel, phân quyền file, Cgroups, tạo user Linux, cài SSL certbot) được ủy quyền cho **8 Binary C SUID** đặt trong `/opt/wpc/bin/`.
- Quyền tập tin: `chmod 4750 /opt/wpc/bin/*`, sở hữu bởi `root:wpc`.

### 5.2. Danh Sách 8 Gatekeepers
1. **`user` (`user.c`):** Tạo/khóa/xóa Linux User, cấp phát thư mục home, cấu hình quota đĩa cứng (`setquota`).
2. **`cgroup` (`cgroup.c`):** Tạo slice cgroups v2 phân cấp, gán `cpu.max`, `memory.max`, `io.weight`, `pids.max`.
3. **`cage` (`cage.c`):** Khởi tạo Linux Namespaces (`NEWNS|NEWPID|NEWIPC|NEWUTS`), chroot/pivot_root, gắn private `/tmp` và kích hoạt PID 1 Subreaper.
4. **`vhost` (`vhost.c`):** Sinh cấu hình VirtualHost (OpenLiteSpeed/Nginx), reload máy chủ web, cài đặt và gia hạn SSL Certbot.
5. **`db` (`db.c`):** Tạo MySQL database, database user, cấp quyền `GRANT`, dump database và optimize table.
6. **`app` (`app.c`):** Cài đặt/gỡ bỏ gói ứng dụng qua APT/DNF, quản lý service systemd, cấu hình PHP Extension.
7. **`wp` (`wp.c`):** Chạy lệnh WP-CLI an toàn bên trong lồng cách ly Cage của chính người dùng website.
8. **`template` (`template.c`):** Biên dịch và áp dụng mẫu cấu hình vhost theo hệ thống kế thừa 3 cấp.

### 5.3. Quy Chuẩn Giao Tiếp Backend $\leftrightarrow$ Gatekeeper
- Backend Python gọi gatekeeper thông qua `asyncio.create_subprocess_exec("/opt/wpc/bin/<gatekeeper>", *args)`.
- Tất cả các C gatekeeper kiểm tra số lượng tham số, làm sạch ký tự độc hại (`sanitize_input`), và **luôn trả về định dạng JSON chuẩn**:
  ```json
  {"status": "success", "message": "Resource configured successfully", "data": {...}}
  {"status": "error", "message": "Invalid parameter or permission denied"}
  ```

---

## 6. Kiểm Soát Tài Nguyên: Cgroups v2 Phân Cấp & Linux Disk Quota

WPC sử dụng kiến trúc **Khóa Trần Phân Cấp 2 Tầng (Hierarchical Two-Tier Locking)** trên Cgroups v2:

```text
/sys/fs/cgroup/wpc/
└── user_{user_id}/                   # Tầng 1: Tổng hạn mức User Package (User dtnguyen)
    ├── cpu.max = 200000 100000       # 200% CPU (2 Cores)
    ├── memory.max = 2147483648       # 2048 MB RAM
    ├── pids.max = 200                # Tối đa 200 tiến trình
    │
    ├── web_{domain_slug_1}/          # Tầng 2: Website Plan cho Website 1
    │   ├── cpu.max = 100000 100000   # 100% CPU
    │   ├── memory.max = 1073741824   # 1024 MB RAM
    │   └── pids.max = 100
    │
    └── web_{domain_slug_2}/          # Tầng 2: Website Plan cho Website 2
        ├── cpu.max = 100000 100000
        ├── memory.max = 1073741824
        └── pids.max = 100
```

- **Nguyên lý bảo đảm:** Tổng tài nguyên tiêu thụ của các website con không bao giờ vượt quá hạn mức trần của gói User Package tầng 1.
- **Disk Quota:** Sử dụng Linux Filesystem Quota (xfs/ext4) gán trực tiếp theo UID của user Linux:
  ```bash
  setquota -u {username} {soft_limit_kb} {hard_limit_kb} 0 0 {mount_point}
  ```

---

## 7. Lồng Cách Ly Bảo Mật: Cage Engine & PID 1 Reaper

Để ngăn ngừa 100% tình trạng symlink attack, đọc trộm file cấu hình giữa các khách hàng hoặc tiến trình mồ côi làm treo máy chủ:

1. **Linux Namespaces:** Mỗi phiên làm việc của user (hoặc PHP-FPM Pool / SSH) chạy trong Namespace riêng:
   - `CLONE_NEWNS`: Mount namespace riêng biệt.
   - `CLONE_NEWPID`: Không gian tiến trình riêng.
   - `CLONE_NEWIPC`: Cách ly bộ nhớ chia sẻ.
   - `CLONE_NEWUTS`: Hostname riêng.
2. **Private `/tmp`:** Tạo thư mục `/tmp` ảo trên RAM (`tmpfs` 256MB), tự hủy khi phiên kết thúc, không chia sẻ với các user khác.
3. **Che giấu `/home`:** Chỉ mount thư mục `/home/{username}` của chính user đó. Tất cả các thư mục của user khác trong `/home` đều biến mất hoàn toàn.
4. **PID 1 Subreaper (`PR_SET_CHILD_SUBREAPER`):** Khi một tiến trình con tạo các subprocess rồi thoát (dạng daemon hoặc mã độc), tiến trình Cage đóng vai trò Init Process (PID 1 trong namespace) tự động nhận diện và dọn sạch tiến trình mồ côi khi kết thúc, không để lại tiến trình zombie.

---

## 8. Kiến Trúc Web Server & Đa Phiên Bản PHP-FPM

- **Hỗ trợ đồng thời 7 phiên bản PHP:** `5.6`, `7.2`, `7.4`, `8.0`, `8.1`, `8.2`, `8.3`, `8.4`, `8.5`.
- **Độc lập FPM Pool:** Mỗi VirtualHost tạo một pool file tại:
  `/opt/wpc/server/php/{version}/etc/php-fpm.d/{username}_{domain_slug}.conf`
- **Nội dung cấu hình Pool tiêu chuẩn:**
  ```ini
  [{username}_{domain_slug}]
  user = {username}
  group = {username}
  listen = /opt/wpc/server/php/run/{username}_{domain_slug}.sock
  listen.owner = wpc
  listen.group = wpc
  listen.mode = 0660
  pm = ondemand
  pm.max_children = 20
  pm.process_idle_timeout = 60s
  php_admin_value[session.save_path] = /home/{username}/domains/{domain}/.tmp
  php_admin_value[upload_tmp_dir] = /home/{username}/domains/{domain}/.tmp
  php_admin_value[open_basedir] = /home/{username}/domains/{domain}:/opt/wpc/server/php/share:/tmp
  ```

---

## 9. Module WordPress Toolkit Toàn Diện

WPC tích hợp cỗ máy quản trị WordPress mạnh mẽ:
1. **Cài đặt 1-Click:** Tải bản WordPress mới nhất (hoặc bản chỉ định), tự động tạo MariaDB database, sinh mật khẩu an toàn, sinh chuỗi muối salts 64 ký tự, cấu hình file `wp-config.php` với Redis Cache / LiteSpeed Cache sẵn sàng.
2. **One-Click SSO:** Đăng nhập thẳng vào trang quản trị `wp-admin` chỉ bằng 1 nút bấm từ giao diện WPC mà không cần nhập username/mật khẩu (sử dụng WP-CLI sinh token đăng nhập dùng 1 lần).
3. **Quản lý Plugin & Theme:** Quét, liệt kê, kích hoạt, vô hiệu hóa, cập nhật hoặc gỡ bỏ plugin/theme trực tiếp trên bảng điều khiển.
4. **Staging / Clone:** Nhân bản 1-click từ bản production sang tên miền staging (subdomain), tự động thay thế chuỗi URL an toàn không phá vỡ dữ liệu PHP serialized.
5. **Security Hardening:**
   - Chặn thực thi mã PHP trong `/wp-content/uploads/`.
   - Vô hiệu hóa tệp nhạy cảm: `xmlrpc.php`, `wp-config.php`, `.env`.
   - Tắt tính năng File Editor trong trang quản trị WordPress (`DISALLOW_FILE_EDIT`).
   - Ẩn thông tin phiên bản WordPress generator.

---

## 10. Lược Đồ Cơ Sở Dữ Liệu SQLite (12 Bảng Chuẩn)

Database đặt tại: `/opt/wpc/data/panel.db` (chạy SQLite 3 với `PRAGMA journal_mode = WAL;` và `PRAGMA busy_timeout = 5000;`).

1. **`packages`:** Lưu trữ cả 2 loại gói:
   - `plan_type = 'user'`: User Package cấp cho tài khoản User.
   - `plan_type = 'website'`: Website Plan cấp cho từng website (`max_domains = 1`).
2. **`users`:** Danh sách tài khoản (`role IN ('admin', 'reseller', 'user')`, trong đó UI hiển thị `user/reseller` là **User**).
3. **`domains`:** VirtualHosts (`domain_name`, `user_id`, `package_id`, `php_version`, `document_root`, `status`).
4. **`databases`:** Cơ sở dữ liệu MariaDB (`db_name`, `user_id`, `charset`).
5. **`database_users`:** Người dùng database (`db_user`, `user_id`).
6. **`php_versions`:** Danh sách phiên bản PHP đã cài đặt trên máy chủ.
7. **`installed_apps`:** Trạng thái các ứng dụng trong App Store (OpenLiteSpeed, Nginx, MariaDB, Redis, phpMyAdmin...).
8. **`firewall_rules`:** Các quy tắc tường lửa cổng mạng (UFW / Iptables).
9. **`cron_jobs`:** Các tác vụ định kỳ của người dùng (`schedule`, `command`, `status`).
10. **`ssl_certificates`:** Chứng chỉ SSL (`provider`, `cert_path`, `key_path`, `expires_at`, `auto_renew`).
11. **`backups`:** Bản sao lưu (`backup_type`, `storage_type`, `file_path`, `file_size_bytes`).
12. **`tasks`:** Tiến trình chạy nền (`task_type`, `status`, `progress`, `log_path`).

---

## 11. Đặc Tả Toàn Diện Hệ Thống REST API (/api/v1) & WebSocket

| Nhóm API | Method & Endpoint | Quyền hạn | Mô tả chức năng |
| :--- | :--- | :--- | :--- |
| **Auth** | `POST /api/v1/auth/login` | Public | Đăng nhập nhận JWT Token, tự động chuẩn hóa vai trò |
| | `GET /api/v1/auth/me` | Authenticated | Lấy thông tin user hiện tại và hạn mức được cấp |
| **Users** | `GET /api/v1/users/customers` | Admin | Lấy danh sách tài khoản Users (Khách hàng) |
| | `POST /api/v1/users/customers` | Admin | Tạo User mới và khởi tạo cgroup slice |
| | `POST /api/v1/users/impersonate/{id}` | Admin | Đăng nhập dưới quyền User (Login as User) |
| | `GET /api/v1/users/plans` | All | Lấy danh sách gói. **Admin thấy User Package, User chỉ thấy Website Plan** |
| | `POST /api/v1/users/plans` | Admin, User | Tạo User Package (Admin) hoặc Website Plan (User/Admin) |
| **Domains** | `GET /api/v1/domains` | Authenticated | Danh sách website thuộc quyền quản lý |
| | `POST /api/v1/domains` | User, Admin | Tạo website mới, gắn FPM pool và cgroups |
| | `DELETE /api/v1/domains/{id}` | User, Admin | Xóa VirtualHost và dọn dẹp dữ liệu |
| | `POST /api/v1/domains/{id}/ssl` | User, Admin | Cài đặt / Gia hạn SSL Let's Encrypt |
| | `POST /api/v1/domains/{id}/php` | User, Admin | Đổi phiên bản PHP tức thời cho website |
| **WordPress**| `POST /api/v1/wp/install` | User, Admin | Cài đặt WordPress 1-Click |
| | `GET /api/v1/wp/sso/{domain_id}` | User, Admin | Sinh liên kết đăng nhập 1-Click không mật khẩu |
| | `POST /api/v1/wp/clone` | User, Admin | Nhân bản website sang Staging |
| | `POST /api/v1/wp/harden` | User, Admin | Áp dụng các quy tắc Security Hardening |
| **Files** | `POST /api/v1/files/list` | Authenticated | Duyệt thư mục an toàn (Jail check) |
| | `POST /api/v1/files/read` | Authenticated | Đọc nội dung file text / code editor |
| | `POST /api/v1/files/write` | Authenticated | Ghi nội dung file |
| | `POST /api/v1/files/archive` | Authenticated | Nén / Giải nén file ZIP/TAR.GZ |
| **Databases**| `GET /api/v1/databases` | Authenticated | Danh sách database và người dùng database |
| | `POST /api/v1/databases` | Authenticated | Tạo MariaDB database và tự động gán quyền |
| **System** | `GET /api/v1/system/metrics` | Authenticated | CPU, RAM, Disk Quota Cgroups v2 |
| | `GET /api/v1/system/services` | Admin | Trạng thái dịch vụ OLS, Nginx, PHP, MariaDB |
| **WebSocket**| `WS /api/v1/system/ws/task/{id}` | Authenticated | Stream log tiến trình cài đặt / tác vụ thời gian thực |

---

## 12. Kiến Trúc Giao Diện Frontend (Vue 3 Single Page Application)

- **Công nghệ:** Vue 3 CDN (Composition API `setup()`), không cần build bundle phức tạp, nạp trực tiếp qua trình duyệt với thời gian phản hồi tức thì.
- **Hệ thống Dynamic URI Hash Navigation:** Hỗ trợ URL định danh riêng biệt cho từng màn hình:
  - `/#dashboard` : Tổng quan tài nguyên hệ thống / tài nguyên cá nhân.
  - `/#domains` : Quản lý danh sách website (Obsidian Cards).
  - `/#service_plans` : Quản lý User Packages (Admin) hoặc Website Plans (User).
  - `/#customers` : Quản trị danh sách Users (Admin).
  - `/#databases` : Cơ sở dữ liệu MariaDB & phpMyAdmin.
  - `/#files` : Quản lý tệp tin Monaco File Manager.
  - `/#wp` : WordPress Toolkit.
  - `/#apps` : Kho ứng dụng máy chủ 1-Click.
  - `/#mail` : Quản lý hòm thư điện tử.
  - `/#monitoring` : Thống kê tài nguyên Cgroups v2 và phần cứng.
- **Thích ứng giao diện thông minh (Adaptive UI):**
  - Khi là **Administrator**: Hiển thị bảng điều khiển phần cứng vật lý máy chủ, menu `Users`, menu `User Package`, quản trị App Store, phpMyAdmin toàn cục.
  - Khi là **User (Hosting User)**: Hiển thị 4 đồng hồ đo hạn mức cá nhân (Cgroups CPU trần, RAM đã dùng, Disk Quota, Trạng thái Container), menu `Website Plan`, quản lý các website con, ẩn các mục hệ thống không thuộc thẩm quyền.
  - Tên vai trò hiển thị trên toàn bộ UI là **`User`** (loại bỏ hoàn toàn thuật ngữ kỹ thuật `Reseller`).

---

## 13. Bộ Lệnh Quản Trị Hệ Thống (`wpc` CLI) & Vận Hành

Tất cả các thao tác trên máy chủ có thể thực hiện thông qua tiện ích dòng lệnh `/usr/local/bin/wpc`:

```bash
# 1. Cập nhật hệ thống lên bản mới nhất từ GitHub (bảo toàn data)
sudo wpc update

# 2. Cài đặt lại sạch sẽ khi gặp lỗi hệ thống (tự động backup an toàn)
sudo wpc reinstall

# 3. Kiểm tra trạng thái và log hoạt động
sudo wpc status
sudo wpc logs

# 4. Thay đổi cổng truy cập bảng điều khiển
sudo wpc port 8899

# 5. Đặt lại mật khẩu tài khoản quản trị admin
sudo wpc password

# 6. Khởi động lại dịch vụ
sudo wpc restart
```

### Triết lý Zero Conflict Footprint
- Toàn bộ runtime nằm trong `/opt/wpc/`.
- Tự động dò và đổi cổng nếu cổng `8888` bị chiếm dụng khi cài đặt.
- Vận hành an toàn song song với bất kỳ dịch vụ hoặc phần mềm nào khác đang chạy trên máy chủ.
