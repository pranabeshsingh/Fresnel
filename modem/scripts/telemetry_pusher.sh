#!/bin/sh
# 5G Modem Agent & Telemetry Pusher
CONF_FILE="/data/simpleadmin/telemetry.conf"
if [ -f "$CONF_FILE" ]; then
    . "$CONF_FILE"
fi
BASE_URL="${TELEMETRY_BASE_URL:-https://modem.example.com}"
TOKEN="${TELEMETRY_TOKEN:-your_telemetry_secret_token}"
CURL="/usrdata/simpleadmin/bin/curl"
DATA_FILE="/tmp/dashboard_data.json"
LOCK_FILE="/tmp/smd7.lock"
PID_FILE="/tmp/telemetry_pusher.pid"

# Ensure only one instance of telemetry_pusher runs
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null && [ "$OLD_PID" != "$$" ]; then
        exit 0
    fi
fi
echo "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"; exit 0' INT TERM EXIT

# Helper for executing AT command safely with flock
execute_at() {
    CMD="$1"
    perl -e '
        use strict; use warnings; use Fcntl qw(:flock O_RDONLY O_WRONLY);
        my $cmd = $ARGV[0] || "ATI"; $cmd =~ s/\r|\n//g;
        my $dev = "/dev/smd7"; my $lock_file = "/tmp/smd7.lock";
        open(my $lf, ">", $lock_file) or die "Lock error";
        flock($lf, LOCK_EX);
        my ($r, $w); pipe($r, $w);
        my $pid = fork();
        if (!defined $pid) { flock($lf, LOCK_UN); close($lf); exit(1); }
        if ($pid == 0) {
            close $r; sysopen(my $fh, $dev, O_RDONLY) or exit(1);
            my $buf; my $res = "";
            eval {
                local $SIG{ALRM} = sub { die "timeout\n" }; alarm(5);
                while (sysread($fh, $buf, 1024)) {
                    $res .= $buf;
                    last if $res =~ /(?:OK|ERROR|\+CMS ERROR:.*|\+CME ERROR:.*)\r?\n/;
                }
                alarm(0);
            };
            print $w $res; close $w; exit(0);
        }
        close $w; select(undef, undef, undef, 0.05);
        if (sysopen(my $wh, $dev, O_WRONLY)) { syswrite($wh, "$cmd\r\n"); close($wh); }
        my $out = ""; while (my $l = <$r>) { $out .= $l; } close $r; waitpid($pid, 0);
        flock($lf, LOCK_UN); close($lf);
        print $out;
    ' "$CMD"
}

# Helper to sync SMS from SIM to VPS
sync_sms() {
    SMS_JSON=$(perl -e '
        use strict; use warnings; use Fcntl qw(:flock O_RDONLY O_WRONLY);
        my $dev = "/dev/smd7"; my $lock_file = "/tmp/smd7.lock";
        sub at_cmd {
            my ($cmd) = @_;
            open(my $lf, ">", $lock_file) or return "";
            flock($lf, LOCK_EX);
            my ($r, $w); pipe($r, $w);
            my $pid = fork();
            if (!defined $pid) { flock($lf, LOCK_UN); close($lf); return ""; }
            if ($pid == 0) {
                close $r; sysopen(my $fh, $dev, O_RDONLY) or exit(1);
                my $buf; my $res = "";
                eval {
                    local $SIG{ALRM} = sub { die "timeout\n" }; alarm(4);
                    while (sysread($fh, $buf, 1024)) {
                        $res .= $buf;
                        last if $res =~ /(?:OK|ERROR|\+CMS ERROR:.*)\r?\n/;
                    }
                    alarm(0);
                };
                print $w $res; close $w; exit(0);
            }
            close $w; select(undef, undef, undef, 0.05);
            if (sysopen(my $wh, $dev, O_WRONLY)) { syswrite($wh, "$cmd\r\n"); close($wh); }
            my $out = ""; while (my $l = <$r>) { $out .= $l; } close $r; waitpid($pid, 0);
            flock($lf, LOCK_UN); close($lf);
            return $out;
        }
        at_cmd("AT+CNMI=2,1,0,0,0");
        at_cmd("AT+CMGF=1");
        at_cmd("AT+CPMS=\"SM\",\"SM\",\"SM\"");
        my $raw_sm = at_cmd("AT+CMGL=\"ALL\"");
        at_cmd("AT+CPMS=\"ME\",\"ME\",\"ME\"");
        my $raw_me = at_cmd("AT+CMGL=\"ALL\"");
        my $raw = "$raw_sm\n$raw_me";
        my @messages = ();
        my @lines = split(/\r?\n/, $raw);
        for (my $i = 0; $i < @lines; $i++) {
            if ($lines[$i] =~ /\+CMGL:\s*(\d+)\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*(?:,[^,]*,\s*"([^"]+)")?/) {
                my ($idx, $stat, $sender, $date) = ($1, $2, $3, $4 || "Unknown");
                my $body = "";
                if ($i + 1 < @lines && $lines[$i+1] !~ /^\+CMGL:/ && $lines[$i+1] !~ /^OK/ && $lines[$i+1] !~ /^ERROR/) {
                    $body = $lines[++$i];
                }

                # Auto-decode hex body
                if ($body =~ /^[0-9a-fA-F]{4,}$/ && (length($body) % 2 == 0)) {
                    my $dec = "";
                    if (length($body) % 4 == 0) {
                        my @chars = map { chr(hex($_)) } ($body =~ /([0-9a-fA-F]{4})/g);
                        my $candidate = join("", @chars);
                        $dec = $candidate if $candidate =~ /^[\x20-\x7E\s\r\n\t]+$/;
                    }
                    if (!$dec) {
                        my $candidate = pack("H*", $body);
                        $dec = $candidate if $candidate =~ /^[\x20-\x7E\s\r\n\t]+$/;
                    }
                    $body = $dec if $dec;
                }

                # Auto-decode hex sender
                if ($sender =~ /^[0-9a-fA-F]{4,}$/ && (length($sender) % 2 == 0) && $sender !~ /^\+?\d+$/) {
                    my $cand_s = pack("H*", $sender);
                    $sender = $cand_s if $cand_s =~ /^[\x20-\x7E\s]+$/;
                }

                my $is_otp = ($body =~ /OTP|code|verification|password|balance|recharge|validity/i) ? 1 : 0;
                $body =~ s/\\/\\\\/g; $body =~ s/"/\\"/g; $sender =~ s/"/\\"/g; $date =~ s/"/\\"/g;
                push @messages, sprintf("{\"id\":%d,\"status\":\"%s\",\"sender\":\"%s\",\"date\":\"%s\",\"body\":\"%s\",\"is_otp\":%d}",
                    $idx, $stat, $sender, $date, $body, $is_otp);
            }
        }
        print "{\"messages\":[" . join(",", @messages) . "]}";
    ')

    if [ -n "$SMS_JSON" ] && echo "$SMS_JSON" | grep -q '"id":'; then
        RESP=$($CURL -s -X POST "$BASE_URL/api/modem/sms/push" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "$SMS_JSON" \
            --connect-timeout 4 --max-time 8)
        if echo "$RESP" | grep -q '"status":\s*"ok"'; then
            # Clean up processed SMS from modem memory to prevent 23-slot overflow
            perl -e '
                use strict; use warnings; use Fcntl qw(:flock O_RDONLY O_WRONLY);
                my $dev = "/dev/smd7"; my $lock_file = "/tmp/smd7.lock";
                my $json = $ARGV[0] || "";
                while ($json =~ /"id":\s*(\d+)/g) {
                    my $idx = $1;
                    open(my $lf, ">", $lock_file) or next;
                    flock($lf, LOCK_EX);
                    if (sysopen(my $wh, $dev, O_WRONLY)) {
                        syswrite($wh, "AT+CMGD=$idx\r\n");
                        close($wh);
                    }
                    flock($lf, LOCK_UN); close($lf);
                    select(undef, undef, undef, 0.05);
                }
            ' "$SMS_JSON"
        fi
    fi
}

LOOP_COUNT=0

while true; do
    LOOP_COUNT=$((LOOP_COUNT + 1))

    # 1. Push Telemetry
    if [ -f "$DATA_FILE" ]; then
        $CURL -s -X POST "$BASE_URL/api/telemetry" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            --data-binary @"$DATA_FILE" \
            --connect-timeout 4 \
            --max-time 6 >/dev/null 2>&1
    fi

    # 2. Poll Command Queue
    POLL_RESP=$($CURL -s "$BASE_URL/api/modem/command/poll" \
        -H "Authorization: Bearer $TOKEN" \
        --connect-timeout 3 --max-time 5)

    if [ -n "$POLL_RESP" ] && echo "$POLL_RESP" | grep -q '"id"'; then
        # Parse commands using perl
        perl -e '
            use strict; use warnings;
            my $json_str = $ARGV[0] || "";
            # Simple extractor for [{"id":1,"command_type":"AT","payload":"ATI"}]
            while ($json_str =~ /\{\s*"id"\s*:\s*(\d+)\s*,\s*"command_type"\s*:\s*"([^"]+)"\s*,\s*"payload"\s*:\s*"([^"]*)"\s*\}/g) {
                my ($id, $type, $payload) = ($1, $2, $3);
                $payload =~ s/\\n/\n/g; $payload =~ s/\\"/"/g;
                print "$id\t$type\t$payload\n";
            }
        ' "$POLL_RESP" | while IFS="$(printf '\t')" read -r CMD_ID CMD_TYPE CMD_PAYLOAD; do
            if [ -n "$CMD_ID" ]; then
                OUT=""
                STATUS="done"

                case "$CMD_TYPE" in
                    AT)
                        OUT=$(execute_at "$CMD_PAYLOAD")
                        ;;
                    BAND_LOCK)
                        OUT=$(execute_at "$CMD_PAYLOAD")
                        ;;
                    USSD)
                        OUT=$(perl -e '
                            use strict; use warnings; use Fcntl qw(:flock O_RDONLY O_WRONLY);
                            my $code = $ARGV[0];
                            # Set CUSD
                            system("perl -e '\''use strict; use warnings; my \$c = \$ARGV[0]; ... '\''");
                        ' "$CMD_PAYLOAD")
                        if [ -z "$OUT" ]; then
                            OUT=$(execute_at "AT+CUSD=1,\"$CMD_PAYLOAD\",15")
                        fi
                        ;;
                    REBOOT)
                        OUT="Rebooting modem in 2 seconds..."
                        # Post result before actual reboot
                        PAYLOAD_JSON=$(perl -e '
                            use strict; use warnings;
                            my ($id, $st, $out) = @ARGV;
                            $out =~ s/\\/\\\\/g; $out =~ s/"/\\"/g; $out =~ s/\n/\\n/g; $out =~ s/\r/\\r/g;
                            print "{\"command_id\":$id,\"status\":\"$st\",\"output\":\"$out\"}";
                        ' "$CMD_ID" "$STATUS" "$OUT")
                        $CURL -s -X POST "$BASE_URL/api/modem/command/result" \
                            -H "Authorization: Bearer $TOKEN" \
                            -H "Content-Type: application/json" \
                            -d "$PAYLOAD_JSON" >/dev/null 2>&1
                        sleep 1
                        /sbin/reboot
                        exit 0
                        ;;
                    BACKUP)
                        tar -czf /tmp/modem_backup.tar.gz -C /data simpleadmin 2>/dev/null
                        if [ -f /tmp/modem_backup.tar.gz ]; then
                            $CURL -s -X POST "$BASE_URL/api/modem/backup/push" \
                                -H "Authorization: Bearer $TOKEN" \
                                -H "Content-Type: application/octet-stream" \
                                --data-binary "@/tmp/modem_backup.tar.gz" >/dev/null 2>&1
                            OUT="Config backup uploaded successfully ($(ls -lh /tmp/modem_backup.tar.gz | awk '{print $5}'))"
                            rm -f /tmp/modem_backup.tar.gz
                        else
                            OUT="Backup creation failed"
                            STATUS="error"
                        fi
                        ;;
                    SIM_PIN)
                        OUT=$(/usrdata/simpleadmin/scripts/sim_pin_helper.pl $CMD_PAYLOAD 2>&1)
                        [ $? -ne 0 ] && STATUS="error"
                        ;;
                    SMS)
                        OUT=$(perl /usrdata/simpleadmin/scripts/send_sms.pl "$CMD_PAYLOAD" 2>&1)
                        [ $? -ne 0 ] && STATUS="error"
                        ;;
                    *)
                        OUT="Unknown command type: $CMD_TYPE"
                        STATUS="error"
                        ;;
                esac

                # Send result back to VPS
                PAYLOAD_JSON=$(perl -e '
                    use strict; use warnings;
                    my ($id, $st, $out) = @ARGV;
                    $out =~ s/\\/\\\\/g; $out =~ s/"/\\"/g; $out =~ s/\n/\\n/g; $out =~ s/\r/\\r/g;
                    print "{\"command_id\":$id,\"status\":\"$st\",\"output\":\"$out\"}";
                ' "$CMD_ID" "$STATUS" "$OUT")

                $CURL -s -X POST "$BASE_URL/api/modem/command/result" \
                    -H "Authorization: Bearer $TOKEN" \
                    -H "Content-Type: application/json" \
                    -d "$PAYLOAD_JSON" \
                    --connect-timeout 4 --max-time 6 >/dev/null 2>&1
            fi
        done
    fi

    # 3. Periodically Sync SMS (every ~30s / 6 loops @ 5s each)
    if [ $((LOOP_COUNT % 6)) -eq 0 ]; then
        sync_sms &
    fi

    sleep 5
done
