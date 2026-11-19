# Made by the app at every start (init-mqtt); changes here are lost. Use the customize option.
user root

# The log goes to the log pipe (stdout): the app's log, and the dashboard's client list
log_dest stdout
{{ if .log_topic }}log_dest topic
{{ end }}{{ range .log_types }}log_type {{ . }}
{{ end }}log_timestamp true
log_timestamp_format %Y-%m-%dT%H:%M:%S
connection_messages true

persistence true
persistence_location /data
# $SYS every 10 seconds, for the dashboard's numbers and history
sys_interval 10

# max_queued_messages is in effect the most entities Home Assistant can take in while it starts
max_queued_messages 8192

# Logins: the files backend (system users and local logins) and Home Assistant's users through nginx
auth_plugin /usr/share/mosquitto/go-auth.so
# Allow / in client IDs and usernames, as before Mosquitto 2.1 (the ACL file uses no %c or %u)
auth_plugin_deny_special_chars false
auth_opt_backends files,http
auth_opt_hasher pbkdf2
auth_opt_cache true
auth_opt_auth_cache_seconds 300
auth_opt_auth_jitter_seconds 30
auth_opt_acl_cache_seconds 300
auth_opt_acl_jitter_seconds 30
auth_opt_log_level {{ if .debug }}debug{{ else }}error{{ end }}
auth_opt_files_password_path /etc/mosquitto/pw
auth_opt_files_acl_path /etc/mosquitto/acl
auth_opt_http_host 127.0.0.1
auth_opt_http_port 80
auth_opt_http_getuser_uri /authentication
auth_opt_http_superuser_uri /superuser
auth_opt_http_aclcheck_uri /acl

{{ if .customize }}
include_dir /share/{{ .customize_folder }}
{{ end }}

listener 1883
protocol mqtt

listener 1884
protocol websockets
{{ if .ssl }}
listener 8883
protocol mqtt
cafile {{ .cafile }}
certfile {{ .certfile }}
keyfile {{ .keyfile }}
require_certificate {{ .require_certificate }}

listener 8884
protocol websockets
cafile {{ .cafile }}
certfile {{ .certfile }}
keyfile {{ .keyfile }}
require_certificate {{ .require_certificate }}
{{ end }}
