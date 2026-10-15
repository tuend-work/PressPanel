docRoot                   $VH_ROOT/domains/{{DOMAIN}}/public_html
vhDomain                  {{DOMAIN}}
vhAliases                 www.{{DOMAIN}}
adminEmails               admin@{{DOMAIN}}
enableGzip                1

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

scripthandler {
  add                     lsapi:lsphp{{PHP_VERSION}} php
}
