<div align="center">

# ⚡ PressPanel

### Thế Hệ Control Panel Độc Lập, Hiệu Năng Cao & Siêu Nhẹ Tối Ưu Cho WordPress

[![Version](https://img.shields.io/badge/version-0.2.1-blue.svg)](VERSION)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![OS](https://img.shields.io/badge/OS-Ubuntu%20%7C%20Debian%20%7C%20AlmaLinux%20%7C%20Rocky-orange.svg)](#yêu-cầu-hệ-thống)
[![License](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)

*Kết hợp trải nghiệm quản trị Domain dạng Card trực quan của **Plesk**, kho ứng dụng 1-click của **aaPanel**, lồng cách ly bảo mật **Cage & Cgroups v2**, và cỗ máy tối ưu **WordPress Toolkit**.*

---

</div>

## 🌟 Tính Năng Nổi Bật

* **🚀 Siêu Nhẹ & Hiệu Năng Vượt Trội:**
  * Backend **Python (FastAPI + Asyncio)** chạy trên **Uvicorn**: Chỉ tiêu tốn ~35MB RAM.
  * Tích hợp máy chủ web tốc độ cao **OpenLiteSpeed (LSCache)** và **Nginx FastCGI Cache**.
* **🛡️ Bảo Mật Tuyệt Đối với 8 C Gatekeepers (SUID):**
  * Tầng Web API chạy dưới user không có quyền quản trị (`presspanel:presspanel`).
  * Mọi can thiệp hệ thống, kernel và file được thực thi bởi **8 binary C nhỏ gọn (chmod 4750 root:presspanel)**, chống 100% leo thang đặc quyền.
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
* **📚 Tài Liệu API Tự Động (Swagger UI):**
  * Tự động sinh giao diện tương tác trực tiếp tại `/docs`, hỗ trợ gọi thử toàn bộ REST API trên trình duyệt.

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

### Cách 1: Cài Đặt Nhanh 1 Dòng (Khuyên dùng)

Đăng nhập vào VPS/Server của bạn qua SSH với quyền `root` và chạy lệnh duy nhất:

```bash
curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/install.sh | sudo bash
```

> [!TIP]
> Script sẽ tự động nhận diện hệ điều hành, cài đặt các gói phụ thuộc, biên dịch 8 module C Gatekeeper, thiết lập Python Virtualenv, nạp Database SQLite và đăng ký dịch vụ `systemd`. Quá trình diễn ra hoàn toàn tự động trong khoảng 1–2 phút.

---

### Cách 2: Cài Đặt Thủ Công từ Kho GitHub

```bash
# 1. Clone repository
git clone https://github.com/tuend-work/PressPanel.git /tmp/PressPanel
cd /tmp/PressPanel

# 2. Thực thi file cài đặt
sudo bash install.sh
```

---

## 🔑 Thông Tin Đăng Nhập Mặc Định

Sau khi cài đặt hoàn tất, bạn có thể truy cập ngay vào giao diện quản trị:

* **Đường dẫn Web Panel:** `http://<IP_MAY_CHU>:8888`
* **Tài liệu Tương tác API (Swagger UI):** `http://<IP_MAY_CHU>:8888/docs`
* **Tài khoản quản trị mặc định:**
  * **Username:** `admin`
  * **Password:** `admin123456`

> [!IMPORTANT]
> Hãy đổi mật khẩu tài khoản quản trị ngay sau khi đăng nhập lần đầu tiên tại mục cài đặt tài khoản.

---

## 🌐 Các Cổng Mạng Cần Mở (Firewall Ports)

Đảm bảo bạn đã mở các cổng sau trên Cloud Provider (AWS, DigitalOcean, Hetzner, Linode, v.v.):

| Cổng | Giao thức | Dịch vụ |
| :---: | :---: | :--- |
| **8888** | TCP | Giao diện Quản trị PressPanel & REST API |
| **80** | TCP | HTTP Web Traffic (OpenLiteSpeed / Nginx) |
| **443** | TCP | HTTPS SSL Web Traffic & HTTP/3 QUIC |
| **7080** | TCP | OpenLiteSpeed WebAdmin Console (Tùy chọn) |
| **22** | TCP | Quản trị SSH Server |

---

## 📂 Quy Hoạch Thư Mục Hệ Thống (`/opt/presspanel`)

```
/opt/presspanel/
├── bin/                  # 8 Binary C SUID Gatekeeper (chmod 4750 root:presspanel)
├── app/                  # Mã nguồn Backend Python FastAPI
├── conf/                 # File cấu hình trung tâm (presspanel.conf giống directadmin.conf)
├── venv/                 # Môi trường ảo Python độc lập (FastAPI + Uvicorn)
├── static/               # Thư mục Web UI tĩnh (Vue 3 Single Page Application)
├── data/                 # SQLite Database (panel.db chế độ WAL)
├── logs/                 # Access log, Error log và Streaming Task log
├── scripts/              # Bộ script Bash quản trị (vhost_manager.sh, wp_manager.sh...)
├── server/               # Dịch vụ đã cài đặt (OpenLiteSpeed, Nginx, PHP, MariaDB)
└── templates/            # Hệ thống mẫu cấu hình kế thừa 3 cấp (User -> Package -> Default)
```

> [!NOTE]
> **File cấu hình trung tâm (`/opt/presspanel/conf/presspanel.conf`):** Tương tự như `directadmin.conf` của DirectAdmin, toàn bộ thông số cốt lõi (cổng port, SSL panel, session, default webserver, quota, đường dẫn) đều được cấu hình theo định dạng `key=value` trực quan, dễ chỉnh sửa và tự động nạp khi khởi động panel.

---

## 🛠️ Quản Lý Dịch Vụ Hệ Thống

PressPanel được quản lý như một dịch vụ nền Linux tiêu chuẩn thông qua `systemd`:

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
