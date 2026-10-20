docRoot                   $VH_ROOT/domains/{{DOMAIN}}/public_html
vhDomain                  {{DOMAIN}}
vhAliases                 www.{{DOMAIN}}
adminEmails               admin@{{DOMAIN}}
enableGzip                1
enableBr                  1

indexFiles                index.php, index.html, index.htm

accessControl {
  allow                   *
}

errorlog $VH_ROOT/domains/{{DOMAIN}}/logs/error.log {
  useServer               0
  logLevel                DEBUG
  rollingSize             10M
}

accesslog $VH_ROOT/domains/{{DOMAIN}}/logs/access.log {
  useServer               0
  rollingSize             10M
  keepDays                30
}

context / {
  type                    null
  location                $DOC_ROOT/
  allow                   *
  addDefaultCharset       off
  extraHeaders            <<<END_extraHeaders
X-Frame-Options: SAMEORIGIN
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), camera=(), microphone=()
END_extraHeaders
}

rewrite  {
  enable                  1
  autoLoadHtaccess        1
  rules                   <<<END_rules
# ========================================================
# WPCloud Security Hardening: Chặn đứng các vector tấn công
# ========================================================
# 1. Cho phép SSL ACME challenge (Let's Encrypt / Certbot)
RewriteRule ^/\.well-known/ - [L]

# 2. Chặn truy cập file ẩn, file cấu hình và VCS repository
RewriteRule ^/(\.git|\.env|\.user\.ini|\.svn|composer\.(json|lock)|package\.json|\.sql|\.bak|\.log) - [F,L]

# 3. Bảo vệ file cấu hình WordPress wp-config.php
RewriteRule ^/wp-config\.php$ - [F,L]

# 4. Chống thực thi mã độc PHP trong thư mục uploads và cache
RewriteRule ^/(wp-content/uploads|cache)/.*\.php$ - [F,L]

# 5. Chặn XML-RPC chống tấn công Brute-force & DDoS WordPress
RewriteRule ^/xmlrpc\.php$ - [F,L]
END_rules
}

extprocessor lsphp_{{USERNAME}} {
  type                    lsapi
  address                 uds://tmp/lshttpd/lsphp_{{USERNAME}}.sock
  maxConns                10
  env                     LSAPI_CHILDREN=10
  initTimeout             60
  retryTimeout            0
  persistConn             1
  respBuffer              0
  autoStart               1
  path                    /usr/local/lsws/lsphp{{PHP_VERSION}}/bin/lsphp
  backlog                 100
  instances               1
  runOn                   1
  extUser                 {{USERNAME}}
  extGroup                {{USERNAME}}
  priority                0
  memSoftLimit            1024M
  memHardLimit            2048M
  procSoftLimit           200
  procHardLimit           300
}

scripthandler {
  add                     lsapi:lsphp_{{USERNAME}} php
}
