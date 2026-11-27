<?php
declare(strict_types=1);

// Đảm bảo session save path và upload tmp dir luôn có quyền ghi tránh lỗi HTTP 500
$tmpDir = __DIR__ . '/tmp';
if (!is_dir($tmpDir)) {
    @mkdir($tmpDir, 0777, true);
}
@chmod($tmpDir, 0777);
@ini_set('session.save_path', $tmpDir);
@ini_set('upload_tmp_dir', $tmpDir);
@ini_set('display_errors', '0');
@ini_set('log_errors', '1');
if (!is_dir(__DIR__ . '/logs')) {
    @mkdir(__DIR__ . '/logs', 0755, true);
}
@ini_set('error_log', __DIR__ . '/logs/php_error.log');

$cfg['blowfish_secret'] = 'WPCloudPMASecretKey20261016Secure';

$i = 0;
$i++;
$cfg['Servers'][$i]['host'] = '127.0.0.1';
$cfg['Servers'][$i]['port'] = '3306';
$cfg['Servers'][$i]['connect_type'] = 'tcp';
$cfg['Servers'][$i]['extension'] = 'mysqli';
$cfg['Servers'][$i]['compress'] = false;
$cfg['Servers'][$i]['AllowNoPassword'] = false;
$cfg['Servers'][$i]['AllowRoot'] = true;
$cfg['Servers'][$i]['SessionSavePath'] = $tmpDir;

// Hỗ trợ chế độ Single Sign-On (SSO) chỉ khi có cookie SignonSession và không đang submit form đăng nhập thường
if (!empty($_COOKIE['SignonSession']) && empty($_POST['pma_username'])) {
    $cfg['Servers'][$i]['auth_type'] = 'signon';
    $cfg['Servers'][$i]['SignonSession'] = 'SignonSession';
    $signonFile = file_exists(__DIR__ . '/presspanel_signon.php') ? 'presspanel_signon.php' : 'wpc_signon.php';
    $cfg['Servers'][$i]['SignonURL'] = $signonFile;
    $cfg['Servers'][$i]['LogoutURL'] = $signonFile . '?logout=1';
} else {
    $cfg['Servers'][$i]['auth_type'] = 'cookie';
}

$cfg['UploadDir'] = '';
$cfg['SaveDir'] = '';
$cfg['TempDir'] = $tmpDir;
$cfg['SendErrorReports'] = 'never';
$cfg['ShowPhpInfo'] = true;
$cfg['MaxRows'] = 50;
