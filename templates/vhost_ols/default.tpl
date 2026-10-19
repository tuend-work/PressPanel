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

rewrite  {
  enable                  1
  autoLoadHtaccess        1
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
