<?php
/**
 * Plugin Name: WPC Optimize - Editor
 * Description: Tắt Gutenberg và trình chỉnh sửa widget dạng block, dùng Classic Editor mặc định.
 * Author: WPCloud.vn
 * Version: 1.0.0
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// Cài đặt Classic Editor mặc định.
add_filter( 'use_block_editor_for_post', '__return_false' );
add_filter( 'use_widgets_block_editor', '__return_false' );
