<?php
/**
 * Plugin Name: WPC Optimize - Admin
 * Description: Dọn dashboard WordPress và admin bar để wp-admin nhẹ hơn.
 * Author: WPCloud.vn
 * Version: 1.0.0
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// Xoá các dashboard widget gây chậm trong admin WP.
add_action( 'wp_dashboard_setup', 'wpc_optimize_remove_dashboard_widgets', 999 );
function wpc_optimize_remove_dashboard_widgets() {
    $widgets = array(
        'dashboard_right_now',
        'dashboard_activity',
        'dashboard_primary',
        'woocommerce_dashboard_recent_reviews',
        'dashboard_site_health',
        'dashboard_quick_press',
        'rank_math_dashboard_widget',
        'dashboard_rediscache',
    );

    foreach ( $widgets as $widget_id ) {
        remove_meta_box( $widget_id, 'dashboard', 'normal' );
        remove_meta_box( $widget_id, 'dashboard', 'side' );
    }
}

// Remove logo WordPress trên admin bar.
add_action( 'wp_before_admin_bar_render', 'wpc_optimize_admin_bar_remove_logo', 0 );
function wpc_optimize_admin_bar_remove_logo() {
    global $wp_admin_bar;

    if ( is_object( $wp_admin_bar ) ) {
        $wp_admin_bar->remove_menu( 'wp-logo' );
    }
}
