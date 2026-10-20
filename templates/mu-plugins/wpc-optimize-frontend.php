<?php
/**
 * Plugin Name: WPC Optimize - Frontend
 * Description: Dọn HTML header, tắt RSS feed, emoji, query string version, CF7 CSS và tối ưu tải script frontend.
 * Author: WPCloud.vn
 * Version: 1.0.0
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// Disable RSS Feeds.
add_action( 'do_feed', 'wpc_optimize_disable_rss_feeds', 1 );
add_action( 'do_feed_rdf', 'wpc_optimize_disable_rss_feeds', 1 );
add_action( 'do_feed_rss', 'wpc_optimize_disable_rss_feeds', 1 );
add_action( 'do_feed_rss2', 'wpc_optimize_disable_rss_feeds', 1 );
add_action( 'do_feed_atom', 'wpc_optimize_disable_rss_feeds', 1 );
add_action( 'do_feed_rss2_comments', 'wpc_optimize_disable_rss_feeds', 1 );
add_action( 'do_feed_atom_comments', 'wpc_optimize_disable_rss_feeds', 1 );

function wpc_optimize_disable_rss_feeds() {
    wp_safe_redirect( esc_url_raw( home_url( '/' ) ), 301 );
    exit;
}

// Clean WordPress Header.
remove_action( 'wp_head', 'wp_generator' );
remove_action( 'wp_head', 'rsd_link' );
remove_action( 'wp_head', 'wlwmanifest_link' );
remove_action( 'wp_head', 'wp_shortlink_wp_head', 10 );
remove_action( 'wp_head', 'start_post_rel_link', 10 );
remove_action( 'wp_head', 'parent_post_rel_link', 10 );
remove_action( 'wp_head', 'index_rel_link' );
remove_action( 'wp_head', 'adjacent_posts_rel_link', 10 );
remove_action( 'wp_head', 'adjacent_posts_rel_link_wp_head', 10 );

// Remove emoji scripts/styles.
remove_action( 'wp_head', 'print_emoji_detection_script', 7 );
remove_action( 'admin_print_scripts', 'print_emoji_detection_script' );
remove_action( 'wp_print_styles', 'print_emoji_styles' );
remove_action( 'admin_print_styles', 'print_emoji_styles' );

// Tắt CSS mặc định của Contact Form 7.
add_filter( 'wpcf7_load_css', '__return_false' );

// Loại bỏ Query String trong CSS/JS WordPress.
add_filter( 'style_loader_src', 'wpc_optimize_remove_cssjs_ver', 10 );
add_filter( 'script_loader_src', 'wpc_optimize_remove_cssjs_ver', 10 );
function wpc_optimize_remove_cssjs_ver( $src ) {
    if ( false !== strpos( $src, '?ver=' ) ) {
        $src = remove_query_arg( 'ver', $src );
    }

    return $src;
}

// Đưa jQuery xuống footer để giảm tài nguyên chặn hiển thị.
add_action( 'wp_enqueue_scripts', 'wpc_optimize_move_jquery_to_footer' );
function wpc_optimize_move_jquery_to_footer() {
    if ( ! is_admin() && function_exists( 'wp_scripts' ) ) {
        wp_scripts()->add_data( 'jquery', 'group', 1 );
        wp_scripts()->add_data( 'jquery-core', 'group', 1 );
        wp_scripts()->add_data( 'jquery-migrate', 'group', 1 );
    }
}

// Tắt toàn bộ resource hints/prefetch/preconnect mặc định.
add_filter( 'wp_resource_hints', 'wpc_optimize_remove_resource_hints', 99999999, 2 );
function wpc_optimize_remove_resource_hints( $urls, $relation_type ) {
    return array();
}
