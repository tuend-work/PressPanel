<?php
/**
 * Plugin Name: WPC Optimize - Compatibility
 * Description: Giảm cảnh báo không cần thiết từ WordPress core/plugin trong một số môi trường production.
 * Author: WPCloud.vn
 * Version: 1.0.0
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// Chặn cảnh báo _load_textdomain_just_in_time gây nhiễu log ở một số plugin/theme.
add_filter( 'doing_it_wrong_trigger_error', 'wpc_optimize_disable_textdomain_just_in_time_notice', 10, 2 );
function wpc_optimize_disable_textdomain_just_in_time_notice( $status, $function_name ) {
    if ( '_load_textdomain_just_in_time' === $function_name ) {
        return false;
    }

    return $status;
}
