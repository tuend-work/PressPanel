<?php
/**
 * Plugin Name: WPC Optimize - Media
 * Description: Ngăn WordPress crop/tạo ảnh phụ khi upload và cho phép upload SVG/CSV.
 * Author: WPCloud.vn
 * Version: 1.0.0
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// Bộ lọc ngăn crop/tạo image size khi tải ảnh lên.
add_action( 'init', 'wpc_optimize_disable_registered_image_sizes' );
function wpc_optimize_disable_registered_image_sizes() {
    foreach ( get_intermediate_image_sizes() as $size ) {
        remove_image_size( $size );
    }
}

add_filter( 'image_resize_dimensions', 'wpc_optimize_disable_image_crop', 10, 6 );
function wpc_optimize_disable_image_crop( $payload, $orig_w, $orig_h, $dest_w, $dest_h, $crop ) {
    return false;
}

add_filter( 'intermediate_image_sizes', 'wpc_optimize_remove_intermediate_image_sizes' );
function wpc_optimize_remove_intermediate_image_sizes( $sizes ) {
    return array();
}

add_filter( 'intermediate_image_sizes_advanced', 'wpc_optimize_remove_default_image_sizes' );
function wpc_optimize_remove_default_image_sizes( $sizes ) {
    $remove_sizes = array(
        'large',
        'thumbnail',
        'medium',
        'medium_large',
        'woocommerce_thumbnail',
        'woocommerce_single',
        'woocommerce_gallery_thumbnail',
        '1536x1536',
        '2048x2048',
        '360x180',
        '150x150',
        '150x133',
        '768x0',
        '0x0',
        '750x375',
        '1140x570',
        '750x536',
        '1140x815',
        '360x504',
        '75x75',
        '100x100',
        '300x300',
        '600x338',
    );

    foreach ( $remove_sizes as $size ) {
        unset( $sizes[ $size ] );
    }

    return $sizes;
}

// Allow SVG/CSV upload.
add_filter( 'upload_mimes', 'wpc_optimize_allow_extra_upload_mimes', 99 );
function wpc_optimize_allow_extra_upload_mimes( $file_types ) {
    $file_types['svg'] = 'image/svg+xml';
    $file_types['csv'] = 'text/csv';

    return $file_types;
}
