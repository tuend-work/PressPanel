<?php
/**
 * Plugin Name: WPC Optimize - Flatsome
 * Description: Ẩn một số thông báo license/maintenance của Flatsome trong wp-admin.
 * Author: WPCloud.vn
 * Version: 1.0.0
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// Nếu cần set buyer name thì mở dòng dưới và tự chịu trách nhiệm theo license đang dùng.
// update_option( 'flatsome_wup_buyer', 'WPCVN' );

add_action( 'admin_head', 'wpc_optimize_hide_flatsome_license_notice' );
function wpc_optimize_hide_flatsome_license_notice() {
    ?>
    <style>
        .license-table {
            display: none !important;
        }
    </style>
    <script>
        document.addEventListener('DOMContentLoaded', function () {
            var notices = document.querySelectorAll('.notice.notice-info h3');

            notices.forEach(function (notice) {
                if (notice.textContent && notice.textContent.indexOf('Flatsome') !== -1) {
                    notice.parentElement.style.display = 'none';
                }
            });

            var licenseNotices = document.querySelectorAll('div.notice.notice-error p strong');

            licenseNotices.forEach(function (notice) {
                if (notice.textContent && notice.textContent.indexOf('License Key') !== -1) {
                    notice.parentElement.parentElement.style.display = 'none';
                }
            });
        });
    </script>
    <?php
}

add_action( 'admin_init', 'wpc_optimize_hide_flatsome_maintenance_admin_notice' );
function wpc_optimize_hide_flatsome_maintenance_admin_notice() {
    remove_action( 'admin_notices', 'flatsome_maintenance_admin_notice' );
}
