<?php
/**
 * Plugin Name: WPC Optimize - Security
 * Description: Tối ưu bảo mật cơ bản: tắt XML-RPC, pingback, ẩn version WordPress, chặn username chứa "test", tắt sửa file trong admin.
 * Author: WPCloud.vn
 * Version: 1.0.0
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// Tắt sửa file theme/plugin trong wp-admin.
if ( ! defined( 'DISALLOW_FILE_EDIT' ) ) {
    define( 'DISALLOW_FILE_EDIT', true );
}

// Tắt XML-RPC và pingback.
add_filter( 'xmlrpc_enabled', '__return_false' );
add_filter( 'pings_open', '__return_false', 9999 );
add_filter( 'pre_update_option_enable_xmlrpc', '__return_false' );
add_filter( 'pre_option_enable_xmlrpc', '__return_zero' );

add_filter( 'wp_headers', 'wpc_optimize_remove_x_pingback' );
function wpc_optimize_remove_x_pingback( $headers ) {
    unset( $headers['X-Pingback'], $headers['x-pingback'] );
    return $headers;
}

// Ẩn version WordPress trong HTML generator.
add_filter( 'the_generator', 'wpc_optimize_remove_wp_version' );
function wpc_optimize_remove_wp_version() {
    return '';
}

// Chặn username chứa từ "test".
add_filter( 'validate_username', 'wpc_optimize_custom_check_username', 10, 2 );
function wpc_optimize_custom_check_username( $valid, $username ) {
    if ( false !== strpos( strtolower( $username ), 'test' ) ) {
        return false;
    }

    return $valid;
}
