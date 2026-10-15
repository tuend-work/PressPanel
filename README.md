<div align="center">

# ⚡ WPC (WPCloud Control Panel)

### Thế Hệ Control Panel Độc Lập, Hiệu Năng Cao & Siêu Nhẹ Tối Ưu Cho WordPress

[![Version](https://img.shields.io/badge/version-0.4.38-blue.svg)](VERSION)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![OS](https://img.shields.io/badge/OS-Ubuntu%20%7C%20Debian%20%7C%20AlmaLinux%20%7C%20Rocky-orange.svg)](#yêu-cầu-hệ-thống)
[![License](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)

*Bảng điều khiển máy chủ độc lập với quản trị Website 360°, cấu trúc đa phiên bản PHP 5.6 - 8.5 kèm Pool FPM độc lập, kho ứng dụng 1-Click, lồng cách ly bảo mật Cage & Cgroups v2, và cỗ máy tối ưu WordPress Toolkit.*

---

</div>

## 🌟 Tính Năng Nổi Bật

* **🚀 Siêu Nhẹ & Hiệu Năng Vượt Trội:**
  * Backend **Python (FastAPI + Asyncio)** chạy trên **Uvicorn**: Tiêu tốn cực ít RAM (~35MB).
  * Tích hợp máy chủ web tốc độ cao **OpenLiteSpeed (LSCache)** và **Nginx FastCGI Cache**.
* **🛡️ Bảo Mật Tuyệt Đối với 8 C Gatekeepers (SUID):**
  * Tầng Web API chạy an toàn dưới user hệ thống không có quyền quản trị (`wpc:wpc`).
  * Mọi can thiệp hệ thống, kernel và file được thực thi bởi **8 binary C nhỏ gọn (chmod 4750 root:wpc)**, loại trừ hoàn toàn nguy cơ leo thang đặc quyền.
* **🌐 Quản Trị Website Chuẩn Mới & Đa Phiên Bản PHP:**
  * Hỗ trợ đầy đủ các phiên bản PHP từ **PHP 5.6 đến PHP 8.5**.
  * Cấu trúc website dạng hiện đại:
    `/home/{username}/domains/{domain.com}/{public_html | logs | ssl | .tmp}`
    kèm symlink tự động `/home/{username}/public_html`.
  * Mỗi người dùng website có **PHP-FPM Pool độc lập**, chạy dưới danh tính người dùng Linux tương ứng, tự động cô lập session và upload temp directory.
  * Quản lý chuyên sâu PHP: Quản lý Extension 1-click, chỉnh sửa chỉ thị directives (`memory_limit`, `max_execution_time`...), phpinfo trực tiếp.
* **📦 Lồng Cách Ly Namespace (Cage Engine & PID 1 Reaper):**
  * Mỗi tài khoản được cô lập trong Linux Namespace riêng biệt (`CLONE_NEWNS|NEWPID|NEWIPC|NEWUTS`).
  * Che giấu thư mục `/home` của người dùng khác, `/tmp` cấp phát riêng trên RAM 256MB.
  * Tích hợp Subreaper thu gom tiến trình mồ côi (chống tràn PID / zombie processes).
* **⚖️ Cgroups v2 Phân Cấp & Khóa Trần 2 Lớp:**
  * Quản trị hạn mức tài nguyên theo mô hình phân cấp **Reseller $\rightarrow$ User con**.
  * Chặn triệt để vượt trần CPU, RAM, IOPS và PIDs từ cấp Kernel Linux.
  * Tích hợp **Linux Filesystem Quota (ext4/xfs)** chống đầy ổ đĩa.
* **🛠️ WordPress Toolkit Toàn Diện:**
  * Cài đặt WordPress 1-Click (tự động tạo database, salts ngẫu nhiên, cấu hình tối ưu).
  * Đăng nhập 1-Click tự động không cần mật khẩu (**One-Click SSO**).
  * Nhân bản Staging & Đổi URL an toàn không hỏng dữ liệu serialized.
  * Tự động khóa bảo mật hệ thống (Security Hardening).
* **📚 Trình Quản Lý Tệp & phpMyAdmin 1-Click:**
  * File Manager tích hợp ngay trên giao diện web, chỉnh sửa code nhanh, nén/giải nén ZIP/TAR.GZ.
  * phpMyAdmin tích hợp VirtualHost riêng (Port 888), 1-click cài đặt, cài lại và mở trực tiếp từ mục Databases.

---

## 🖥️ Yêu Cầu Hệ Thống

| Thành phần | Yêu cầu tối thiểu | Khuyến nghị |
| :--- | :--- | :--- |
| **Hệ điều hành** | Ubuntu 22.04/24.04, Debian 11/12, AlmaLinux 9, Rocky Linux 9 | Ubuntu 22.04 LTS / 24.04 LTS |
| **Kiến trúc** | x86_64 (64-bit) | x86_64 |
| **CPU** | 1 Core | 2 Cores trở lên |
| **RAM** | 1 GB | 2 GB trở lên |
| **Dung lượng ổ cứng** | 10 GB trống | SSD / NVMe |

---

## ⚡ Hướng Dẫn Cài Đặt

### Cài Đặt Nhanh 1 Dòng (Khuyên dùng)

Chạy lệnh cài đặt tự động với quyền `root`:

```bash
curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/install.sh | sudo bash
```

Quá trình cài đặt hoàn toàn tự động trong khoảng 1–2 phút:
1. Cài đặt các gói phụ thuộc nền tảng (`gcc`, `make`, `python3-venv`, `sqlite3`...).
2. Tạo tài khoản dịch vụ hệ thống `wpc:wpc`.
3. Biên dịch 8 C SUID Gatekeepers với các cờ bảo mật cao cấp (`-fstack-protector-strong`, `RELRO`, `PIE`).
4. Khởi tạo môi trường ảo Python và Database SQLite WAL.
5. Đăng ký và kích hoạt dịch vụ `systemd` tự khởi động cùng OS.

---

## 📂 Cấu Trúc Triển Khai Hệ Thống (`/opt/presspanel`)

```text
/opt/presspanel/
├── bin/                  # 8 C SUID Gatekeepers (chmod 4750 root:wpc)
├── app/                  # Mã nguồn FastAPI Backend (Asyncio Web Server)
├── conf/                 # File cấu hình trung tâm (presspanel.conf)
├── venv/                 # Môi trường ảo Python độc lập (FastAPI + Uvicorn)
├── static/               # Thư mục Web UI tĩnh (Vue 3 Single Page Application)
├── data/                 # SQLite Database (panel.db chế độ WAL)
├── logs/                 # Access log, Error log và Streaming Task log
├── scripts/              # Bộ script Bash quản trị (vhost_manager.sh, wp_manager.sh, wpc...)
├── server/               # Dịch vụ đã cài đặt (OpenLiteSpeed, Nginx, PHP, MariaDB)
└── templates/            # Hệ thống mẫu cấu hình kế thừa 3 cấp (User -> Package -> Default)
```

> [!NOTE]
> **File cấu hình trung tâm (`/opt/presspanel/conf/presspanel.conf`):** Toàn bộ thông số cốt lõi (cổng port, SSL panel, session, default webserver, quota, đường dẫn) đều được cấu hình theo định dạng `key=value` trực quan, dễ chỉnh sửa và tự động nạp khi khởi động panel.

---

## 🤝 Khả Năng Vận Hành Độc Lập & Song Song (Zero Conflict Footprint)

WPC được thiết kế với triết lý **Cách Ly Tuyệt Đối (Zero Conflict Footprint)**, cho phép **cài đặt và vận hành song song trên cùng một máy chủ** đã có sẵn các dịch vụ khác mà không làm gián đoạn hệ thống:

### 1. Kiến Trúc Không Xung Đột (Isolated Architecture)
* **Thư mục độc lập 100%:** Toàn bộ file thực thi, mã nguồn, log và data nằm gọn trong `/opt/presspanel`.
* **Môi trường Python Virtualenv riêng biệt:** Chạy trong `/opt/presspanel/venv/`, không đụng chạm đến Python hệ thống.
* **Cơ sở dữ liệu độc lập:** Sử dụng SQLite nhúng WAL (`/opt/presspanel/data/panel.db`), không phụ thuộc vào MySQL hệ thống để vận hành panel.
* **Kernel Namespace & Cgroups v2 riêng:** Quản lý tài nguyên tại nhánh riêng `/sys/fs/cgroup/presspanel`.

### 2. Tự Động Xử Lý Trùng Cổng (Smart Port Auto-Detection)
* Khi cài đặt, nếu phát hiện cổng mặc định **`8888`** đang bị chiếm dụng:
  * Bộ cài `install.sh` sẽ **tự động chuyển sang cổng khả dụng tiếp theo (ví dụ: `8889`)** và ghi nhận vào `presspanel.conf`.
* Bạn có thể đổi sang bất kỳ cổng nào tùy ý bất cứ lúc nào chỉ với 1 câu lệnh:
  ```bash
  sudo wpc port 8899
  ```

---

## 🔄 Cập Nhật & Cài Đặt Lại Panel (Update & Reinstall)

### 1. Cập Nhật Lên Bản Mới Nhất Từ GitHub (Update)
Lệnh update sẽ tự động kéo mã nguồn mới nhất, biên dịch lại C Gatekeeper, cập nhật dependencies và chạy migration DB mà **bảo toàn 100% dữ liệu database, website và file cấu hình**:

* **Sử dụng lệnh CLI:**
  ```bash
  sudo wpc update
  ```
* **Hoặc chạy lệnh 1 dòng từ xa:**
  ```bash
  curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/scripts/update.sh | sudo bash
  ```

---

### 2. Cài Đặt Lại Toàn Bộ Panel (Reinstall)
Khi hệ thống gặp sự cố hoặc bạn muốn làm sạch toàn bộ môi trường và cài đặt lại từ đầu:
Script sẽ **tự động sao lưu `panel.db` và `presspanel.conf`** trước khi reset để bạn không bị mất dữ liệu:

* **Sử dụng lệnh CLI:**
  ```bash
  sudo wpc reinstall
  ```

---

## 💻 Bộ Lệnh Quản Trị Hệ Thống (`wpc` CLI)

Sau khi cài đặt, bạn có thể gõ trực tiếp lệnh `wpc` trên terminal máy chủ bất kỳ lúc nào:

| Lệnh | Mô tả |
| :--- | :--- |
| `wpc update` | Cập nhật panel lên bản mới nhất từ GitHub |
| `wpc reinstall` | Cài đặt lại toàn bộ panel (tự động backup an toàn) |
| `wpc status` | Kiểm tra trạng thái hoạt động của dịch vụ panel |
| `wpc restart` | Khởi động lại dịch vụ panel |
| `wpc stop` | Dừng dịch vụ panel |
| `wpc start` | Bật dịch vụ panel |
| `wpc logs` | Xem nhật ký hoạt động thời gian thực (`journalctl`) |
| `wpc version` | Xem phiên bản panel đang cài đặt |
| `wpc password` | Đổi mật khẩu tài khoản quản trị `admin` qua terminal |
| `wpc port <số>` | Thay đổi cổng truy cập panel |
| `wpc help` | Hiển thị menu hướng dẫn các lệnh |

---

## 🛠️ Quản Lý Dịch Vụ Hệ Thống (Systemd)

Bạn cũng có thể điều khiển trực tiếp qua `systemctl`:

```bash
# Xem trạng thái hoạt động của panel
sudo systemctl status presspanel

# Khởi động lại panel
sudo systemctl restart presspanel

# Tắt panel
sudo systemctl stop presspanel

# Bật panel
sudo systemctl start presspanel

# Xem log hoạt động theo thời gian thực
sudo journalctl -u presspanel -f
```

---

## 📄 Tài Liệu Kỹ Thuật

Để tìm hiểu chi tiết về lược đồ cơ sở dữ liệu SQLite, kiến trúc Cgroups v2 phân cấp và đặc tả toàn bộ REST API, vui lòng xem tài liệu:
👉 **[presspanel_master_spec.md](presspanel_master_spec.md)**

---

## 📜 Giấy Phép (License)

Dự án được phát hành dưới giấy phép [MIT License](LICENSE).
Bản quyền thuộc về [tuend-work](https://github.com/tuend-work).
