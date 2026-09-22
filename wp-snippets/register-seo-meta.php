<?php
/**
 * Plugin Name: Register SEO Meta for REST API
 * Description: Exposes Yoast SEO and RankMath meta title/description fields to
 *              the WP REST API so external tools (e.g. the content bot) can
 *              actually set them. Without this, WordPress silently ignores
 *              these fields in any REST create/update request -- no error,
 *              they just never save.
 *
 * Install: copy this file to wp-content/mu-plugins/register-seo-meta.php
 *          (create the mu-plugins folder if it doesn't exist). mu-plugins load
 *          automatically -- no activation step needed, and it can't be
 *          accidentally deactivated from the plugins screen.
 */

add_action('init', function () {
    $fields = [
        // Yoast SEO
        '_yoast_wpseo_title'      => 'string',
        '_yoast_wpseo_metadesc'   => 'string',
        // RankMath
        'rank_math_title'         => 'string',
        'rank_math_description'   => 'string',
    ];

    foreach (['post', 'page'] as $post_type) {
        foreach ($fields as $key => $type) {
            register_post_meta($post_type, $key, [
                'show_in_rest' => true,
                'single'       => true,
                'type'         => $type,
                'auth_callback' => function () {
                    return current_user_can('edit_posts');
                },
            ]);
        }
    }
});
