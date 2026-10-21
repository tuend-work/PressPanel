extprocessor lsphp_{{USERNAME}} {
  type                    lsapi
  address                 uds://tmp/lshttpd/lsphp_{{USERNAME}}.sock
  maxConns                {{MAX_CONNS}}
{{ENV_BLOCK}}
  initTimeout             {{INIT_TIMEOUT}}
  retryTimeout            {{RETRY_TIMEOUT}}
  persistConn             {{PERSIST_CONN}}{{KEEPALIVE_LINE}}
  respBuffer              {{RESP_BUFFER}}
  autoStart               {{AUTO_START}}
  path                    {{LSPHP_BIN}}
  backlog                 {{BACKLOG}}
  instances               {{INSTANCES}}
  runOn                   {{RUN_ON}}
  extUser                 {{EXT_USER}}
  extGroup                {{EXT_GROUP}}{{UMASK_LINE}}{{MAX_IDLE_LINE}}
  priority                {{PRIORITY}}
  memSoftLimit            {{MEM_SOFT_LIMIT}}
  memHardLimit            {{MEM_HARD_LIMIT}}
  procSoftLimit           {{PROC_SOFT_LIMIT}}
  procHardLimit           {{PROC_HARD_LIMIT}}{{NOTES_LINE}}
}
