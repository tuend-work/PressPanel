<?php
/**
 * PressPanel / WPCloud: Single Sign-On (SSO) Handler for phpMyAdmin
 */
declare(strict_types=1);

$tmpDir = __DIR__ . '/tmp';
if (!is_dir($tmpDir)) {
    @mkdir($tmpDir, 0777, true);
}
@chmod($tmpDir, 0777);
@ini_set('session.save_path', $tmpDir);

// 1. Xử lý Đăng xuất (Logout)
if (isset($_GET['logout'])) {
    session_name('SignonSession');
    @session_start();
    $_SESSION = [];
    if (ini_get("session.use_cookies")) {
        $params = session_get_cookie_params();
        setcookie(session_name(), '', time() - 42000,
            $params["path"] ?? '/', $params["domain"] ?? '',
            $params["secure"] ?? false, $params["httponly"] ?? true
        );
        setcookie('phpMyAdmin', '', time() - 42000,
            $params["path"] ?? '/', $params["domain"] ?? '',
            $params["secure"] ?? false, $params["httponly"] ?? true
        );
    }
    @session_destroy();
    header('Location: index.php');
    exit;
}

// 2. Nhận token đăng nhập 1 lần từ URL query hoặc POST
$token = $_GET['token'] ?? $_POST['token'] ?? '';
if (empty($token)) {
    header('HTTP/1.1 400 Bad Request');
    die('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Lỗi SSO phpMyAdmin</title></head><body style="font-family:sans-serif;text-align:center;padding:50px;"><h2>❌ Lỗi xác thực Single Sign-On</h2><p>Thiếu mã token đăng nhập một lần (SSO token).</p><p><a href="index.php">Chuyển đến màn hình đăng nhập phpMyAdmin chuẩn</a></p></body></html>');
}

// 3. Xác minh Token với backend PressPanel nội bộ (đọc port từ presspanel.conf)
$panelPort = '8888';
$confFile = '/opt/presspanel/conf/presspanel.conf';
if (is_file($confFile) && is_readable($confFile)) {
    $confTxt = @file_get_contents($confFile);
    if ($confTxt && preg_match('/^port=(\d+)/m', $confTxt, $mPort)) {
        $panelPort = $mPort[1];
    }
}
$verifyUrl = 'http://127.0.0.1:' . $panelPort . '/api/v1/databases/pma-sso/verify?token=' . urlencode((string)$token);
$ctx = stream_context_create([
    'http' => [
        'timeout' => 5,
        'ignore_errors' => true
    ]
]);
$resp = @file_get_contents($verifyUrl, false, $ctx);
if ($resp === false) {
    header('HTTP/1.1 502 Bad Gateway');
    die('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Lỗi SSO phpMyAdmin</title></head><body style="font-family:sans-serif;text-align:center;padding:50px;"><h2>❌ Không thể kết nối PressPanel API</h2><p>Không thể kết nối đến dịch vụ xác thực SSO trên cổng 8888.</p></body></html>');
}

$data = @json_decode($resp, true);
if (!$data || empty($data['valid'])) {
    header('HTTP/1.1 401 Unauthorized');
    $errMsg = $data['detail'] ?? ($data['message'] ?? 'Token SSO không hợp lệ hoặc đã hết hạn.');
    die('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Lỗi SSO phpMyAdmin</title></head><body style="font-family:sans-serif;text-align:center;padding:50px;"><h2>❌ Xác thực thất bại</h2><p>' . htmlspecialchars((string)$errMsg) . '</p><p><a href="index.php">Quay lại trang đăng nhập phpMyAdmin</a></p></body></html>');
}

// 4. Khởi tạo Signon Session cho phpMyAdmin
$sessionName = 'SignonSession';
ini_set('session.use_cookies', '1');

// Thiết lập cookie tương thích cả HTTP lẫn HTTPS
if (PHP_VERSION_ID >= 70300) {
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => '/',
        'domain' => '',
        'secure' => false,
        'httponly' => true,
        'samesite' => 'Lax'
    ]);
} else {
    session_set_cookie_params(0, '/', '', false, true);
}

session_name($sessionName);
@session_start();

$_SESSION['LAST_SIGNON_URL'] = 'presspanel_signon.php';
$_SESSION['PMA_single_signon_user'] = $data['user'];
$_SESSION['PMA_single_signon_password'] = $data['password'];
$_SESSION['PMA_single_signon_host'] = $data['host'] ?? '127.0.0.1';
$_SESSION['PMA_single_signon_port'] = (int)($data['port'] ?? 3306);

$db = $data['database'] ?? '';
$onlyDb = $data['only_db'] ?? null;
$cfgUpdate = [];
if ($onlyDb !== false && !empty($onlyDb)) {
    if (is_array($onlyDb) || is_string($onlyDb)) {
        $cfgUpdate['only_db'] = $onlyDb;
    } elseif (!empty($db)) {
        $cfgUpdate['only_db'] = $db;
    }
}
$_SESSION['PMA_single_signon_cfgupdate'] = $cfgUpdate;
$_SESSION['PMA_single_signon_HMAC_secret'] = hash('sha256', (string)$token);

// Chuyển hướng ngay sang phpMyAdmin giao diện chính
header('Location: index.php');
exit;
