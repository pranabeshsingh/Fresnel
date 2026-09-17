#!/usr/bin/perl
use strict;
use warnings;
use Fcntl qw(:flock O_RDONLY O_WRONLY);

my $dev = "/dev/smd7";
my $lock_file = "/tmp/smd7.lock";
my $sec_conf = "/data/simpleadmin/sim_security.conf";
my $status_cache = "/tmp/sim_unlock_status.json";
my $failed_flag = "/tmp/sim_auto_unlock_failed";

sub at_cmd {
    my ($cmd, $timeout) = @_;
    $timeout ||= 5;
    $cmd =~ s/\r|\n//g;
    open(my $lf, ">", $lock_file) or return "";
    flock($lf, LOCK_EX);
    my ($r, $w);
    pipe($r, $w);
    my $pid = fork();
    if (!defined $pid) {
        flock($lf, LOCK_UN);
        close($lf);
        return "";
    }
    if ($pid == 0) {
        close $r;
        my $fh;
        if (!sysopen($fh, $dev, O_RDONLY)) {
            exit(1);
        }
        my $buf;
        my $res = "";
        eval {
            local $SIG{ALRM} = sub { die "timeout\n" };
            alarm($timeout);
            while (sysread($fh, $buf, 1024)) {
                $res .= $buf;
                last if $res =~ /(?:OK|ERROR|\+CMS ERROR:.*|\+CME ERROR:.*)\r?\n/;
            }
            alarm(0);
        };
        print $w $res;
        close $w;
        exit(0);
    }
    close $w;
    select(undef, undef, undef, 0.05);
    if (sysopen(my $wh, $dev, O_WRONLY)) {
        syswrite($wh, "$cmd\r\n");
        close($wh);
    }
    my $out = "";
    while (my $l = <$r>) {
        $out .= $l;
    }
    close $r;
    waitpid($pid, 0);
    flock($lf, LOCK_UN);
    close($lf);
    return $out;
}

sub get_saved_pin {
    return "" unless -f $sec_conf;
    my $pin = "";
    my $auto = 0;
    if (open(my $fh, "<", $sec_conf)) {
        while (my $line = <$fh>) {
            chomp $line;
            if ($line =~ /^\s*auto_unlock\s*=\s*(\d+)/i) {
                $auto = int($1);
            }
            elsif ($line =~ /^\s*pin\s*=\s*([0-9]{4,8})/i) {
                $pin = $1;
            }
        }
        close($fh);
    }
    return ($auto == 1) ? $pin : "";
}

sub is_auto_unlock_configured {
    return 0 unless -f $sec_conf;
    my $auto = 0;
    my $has_pin = 0;
    if (open(my $fh, "<", $sec_conf)) {
        while (my $line = <$fh>) {
            chomp $line;
            $auto = 1 if $line =~ /^\s*auto_unlock\s*=\s*1/i;
            $has_pin = 1 if $line =~ /^\s*pin\s*=\s*[0-9]{4,8}/i;
        }
        close($fh);
    }
    return ($auto && $has_pin) ? 1 : 0;
}

sub save_auto_unlock {
    my ($pin) = @_;
    return (0, "PIN must be 4-8 digits") unless defined $pin && $pin =~ /^[0-9]{4,8}$/;
    
    # Secure storage: mode 0600 root only
    if (open(my $fh, ">", "$sec_conf.tmp")) {
        print $fh "# SimpleAdmin SIM Security Configuration\n";
        print $fh "auto_unlock=1\n";
        print $fh "pin=$pin\n";
        print $fh "updated=" . time() . "\n";
        close($fh);
        chmod 0600, "$sec_conf.tmp";
        rename "$sec_conf.tmp", $sec_conf;
        chmod 0600, $sec_conf;
        unlink $failed_flag if -f $failed_flag;
        return (1, "Auto-unlock configured successfully");
    }
    return (0, "Failed to write configuration file");
}

sub clear_auto_unlock {
    if (-f $sec_conf) {
        unlink $sec_conf;
    }
    unlink $failed_flag if -f $failed_flag;
    return (1, "Auto-unlock disabled and saved PIN cleared");
}

sub query_sim_status {
    my $cpin_out = at_cmd("AT+CPIN?");
    my $clck_out = at_cmd('AT+CLCK="SC",2');
    my $cpinr_out = at_cmd("AT+CPINR");

    my $sim_status = "UNKNOWN";
    if ($cpin_out =~ /\+CPIN:\s*([A-Za-z0-9 _-]+)/) {
        $sim_status = $1;
        $sim_status =~ s/^\s+|\s+$//g;
    } elsif ($cpin_out =~ /\+CME ERROR:\s*(\d+|[^\r\n]+)/) {
        my $err = $1;
        if ($err =~ /10|SIM not inserted/i) {
            $sim_status = "NO SIM";
        } else {
            $sim_status = "ERROR ($err)";
        }
    }

    my $pin_lock_enabled = 0;
    if ($clck_out =~ /\+CLCK:\s*1/) {
        $pin_lock_enabled = 1;
    } elsif ($clck_out =~ /\+CLCK:\s*0/) {
        $pin_lock_enabled = 0;
    }

    my $pin_retries = 3;
    my $puk_retries = 10;
    if ($cpinr_out =~ /\+CPINR:\s*SIM PIN\s*(\d+)/i) {
        $pin_retries = int($1);
    }
    if ($cpinr_out =~ /\+CPINR:\s*SIM PUK\s*(\d+)/i) {
        $puk_retries = int($1);
    }

    my $auto_cfg = is_auto_unlock_configured();
    my $auto_failed = (-f $failed_flag) ? 1 : 0;

    return {
        sim_status => $sim_status,
        pin_lock_enabled => $pin_lock_enabled,
        pin_retries => $pin_retries,
        puk_retries => $puk_retries,
        auto_unlock_configured => $auto_cfg,
        auto_unlock_failed => $auto_failed
    };
}

sub execute_auto_unlock {
    my $now = time();
    
    # Precondition 1: Check saved configuration
    my $saved_pin = get_saved_pin();
    if ($saved_pin eq "") {
        return (0, "Auto-unlock not configured or disabled");
    }

    # Precondition 2: Wait up to 35 seconds for baseband to initialize SIM card state
    my $st = "UNKNOWN";
    my $status_info;
    my $wait_start = time();
    while (time() - $wait_start < 35) {
        $status_info = query_sim_status();
        $st = $status_info->{sim_status};
        # Settle once we reach a definitive state
        last if ($st eq "SIM PIN" || $st eq "READY" || $st eq "SIM PUK" || $st eq "NO SIM");
        # Baseband is still initializing (e.g. NOT READY, SIM BUSY)
        select(undef, undef, undef, 2.0);
    }

    if ($st eq "READY") {
        # SIM already unlocked or no PIN needed
        unlink $failed_flag if -f $failed_flag;
        if (open(my $sf, ">", $status_cache)) {
            print $sf sprintf('{"status":"ready","time":%d,"message":"SIM already in READY state"}', $now);
            close($sf);
        }
        return (1, "SIM already in READY state");
    }

    if ($st eq "SIM PUK") {
        # Safeguard: Never attempt PIN unlock if PUK locked
        open(my $ff, ">", $failed_flag);
        print $ff "PUK locked. Manual PUK required.\n";
        close($ff);
        if (open(my $sf, ">", $status_cache)) {
            print $sf sprintf('{"status":"puk_locked","time":%d,"message":"SIM is PUK locked. Auto-unlock aborted."}', $now);
            close($sf);
        }
        return (0, "SIM is PUK locked. Auto-unlock aborted to protect SIM card.");
    }

    if ($st eq "NO SIM") {
        return (0, "No SIM card detected in slot");
    }

    if ($st ne "SIM PIN") {
        return (0, "SIM state is '$st', not 'SIM PIN'");
    }

    # Precondition 3: Safeguard against burning last retry
    my $retries = $status_info->{pin_retries};
    if ($retries <= 1) {
        open(my $ff, ">", $failed_flag);
        print $ff "Only $retries PIN attempt(s) remaining. Auto-unlock aborted.\n";
        close($ff);
        if (open(my $sf, ">", $status_cache)) {
            print $sf sprintf('{"status":"safeguard_abort","time":%d,"message":"Only %d PIN attempt remaining"}', $now, $retries);
            close($sf);
        }
        return (0, "Safeguard triggered: Only $retries PIN attempt remaining. Manual WebUI entry required.");
    }

    # Execute AT+CPIN="<pin>" ONCE
    my $res = at_cmd("AT+CPIN=\"$saved_pin\"", 8);

    # Verify result with polling up to 5 seconds
    my $post_status;
    my $post_st = "UNKNOWN";
    for (1 .. 5) {
        select(undef, undef, undef, 1.0);
        $post_status = query_sim_status();
        $post_st = $post_status->{sim_status};
        last if $post_st eq "READY";
    }

    if ($post_st eq "READY") {
        unlink $failed_flag if -f $failed_flag;
        if (open(my $sf, ">", $status_cache)) {
            print $sf sprintf('{"status":"success","time":%d,"message":"SIM auto-unlocked successfully"}', $now);
            close($sf);
        }
        # Trigger cellular network attach
        at_cmd("AT+COPS=0", 5);
        return (1, "SIM auto-unlocked successfully");
    } else {
        # Mark failed flag to prevent any subsequent auto-retries in this session
        open(my $ff, ">", $failed_flag);
        print $ff sprintf("Auto-unlock failed at %d: %s (retries left: %d)\n", $now, $res, $post_status->{pin_retries});
        close($ff);
        if (open(my $sf, ">", $status_cache)) {
            print $sf sprintf('{"status":"failed","time":%d,"message":"Invalid PIN. Retries remaining: %d"}', $now, $post_status->{pin_retries});
            close($sf);
        }
        return (0, "Auto-unlock failed: Invalid PIN. $post_status->{pin_retries} attempts remaining.");
    }
}

sub manual_unlock {
    my ($pin) = @_;
    return (0, "PIN must be 4-8 digits") unless defined $pin && $pin =~ /^[0-9]{4,8}$/;
    
    my $status_info = query_sim_status();
    if ($status_info->{sim_status} eq "READY") {
        return (1, "SIM is already unlocked");
    }
    if ($status_info->{sim_status} eq "SIM PUK") {
        return (0, "SIM is PUK locked. Please use PUK unlock.");
    }
    
    my $res = at_cmd("AT+CPIN=\"$pin\"", 8);
    my $post_status;
    for (1 .. 4) {
        select(undef, undef, undef, 0.8);
        $post_status = query_sim_status();
        last if $post_status->{sim_status} eq "READY";
    }
    if ($post_status->{sim_status} eq "READY") {
        unlink $failed_flag if -f $failed_flag;
        at_cmd("AT+COPS=0", 5);
        return (1, "SIM unlocked successfully");
    } else {
        return (0, "Unlock failed. Retries remaining: $post_status->{pin_retries}");
    }
}

sub set_pin_lock {
    my ($mode, $pin) = @_;
    $mode = int($mode || 0);
    return (0, "PIN must be 4-8 digits") unless defined $pin && $pin =~ /^[0-9]{4,8}$/;
    
    my $cmd = sprintf('AT+CLCK="SC",%d,"%s"', $mode, $pin);
    my $res = at_cmd($cmd, 8);
    
    my $post_status;
    if ($res =~ /OK/) {
        for (1 .. 3) {
            select(undef, undef, undef, 0.5);
            $post_status = query_sim_status();
            last if $post_status->{pin_lock_enabled} == $mode;
        }
        my $msg = ($mode == 1) ? "SIM PIN lock enabled" : "SIM PIN lock disabled";
        return (1, $msg);
    } else {
        select(undef, undef, undef, 0.5);
        $post_status = query_sim_status();
        return (0, "Failed to update PIN lock. Verify your PIN. Retries remaining: $post_status->{pin_retries}");
    }
}

sub change_pin {
    my ($old_pin, $new_pin) = @_;
    return (0, "Current PIN must be 4-8 digits") unless defined $old_pin && $old_pin =~ /^[0-9]{4,8}$/;
    return (0, "New PIN must be 4-8 digits") unless defined $new_pin && $new_pin =~ /^[0-9]{4,8}$/;
    
    my $cmd = sprintf('AT+CPWD="SC","%s","%s"', $old_pin, $new_pin);
    my $res = at_cmd($cmd, 8);
    
    select(undef, undef, undef, 0.5);
    my $post_status = query_sim_status();
    if ($res =~ /OK/) {
        # If auto-unlock was enabled with old PIN, update it with new PIN
        if (is_auto_unlock_configured()) {
            save_auto_unlock($new_pin);
        }
        return (1, "SIM PIN changed successfully");
    } else {
        return (0, "Failed to change PIN. Verify current PIN. Retries remaining: $post_status->{pin_retries}");
    }
}

sub puk_unlock {
    my ($puk, $new_pin) = @_;
    return (0, "PUK must be 8 digits") unless defined $puk && $puk =~ /^[0-9]{8}$/;
    return (0, "New PIN must be 4-8 digits") unless defined $new_pin && $new_pin =~ /^[0-9]{4,8}$/;
    
    my $cmd = sprintf('AT+CPIN="%s","%s"', $puk, $new_pin);
    my $res = at_cmd($cmd, 10);
    
    my $post_status;
    for (1 .. 4) {
        select(undef, undef, undef, 1.0);
        $post_status = query_sim_status();
        last if $post_status->{sim_status} eq "READY";
    }
    if ($post_status->{sim_status} eq "READY") {
        unlink $failed_flag if -f $failed_flag;
        if (is_auto_unlock_configured()) {
            save_auto_unlock($new_pin);
        }
        at_cmd("AT+COPS=0", 5);
        return (1, "SIM unblocked successfully with PUK. New PIN is active.");
    } else {
        return (0, "PUK unblock failed. PUK retries remaining: $post_status->{puk_retries}");
    }
}

# CLI Argument Processing
my $action = $ARGV[0] || "--status";

if ($action eq "--status") {
    my $info = query_sim_status();
    printf("{\n");
    printf("  \"sim_status\": \"%s\",\n", $info->{sim_status});
    printf("  \"pin_lock_enabled\": %d,\n", $info->{pin_lock_enabled});
    printf("  \"pin_retries\": %d,\n", $info->{pin_retries});
    printf("  \"puk_retries\": %d,\n", $info->{puk_retries});
    printf("  \"auto_unlock_configured\": %d,\n", $info->{auto_unlock_configured});
    printf("  \"auto_unlock_failed\": %d\n", $info->{auto_unlock_failed});
    printf("}\n");
    exit(0);
}
elsif ($action eq "--auto-unlock") {
    my ($ok, $msg) = execute_auto_unlock();
    print "$msg\n";
    exit($ok ? 0 : 1);
}
elsif ($action eq "--unlock") {
    my $pin = $ARGV[1] || "";
    my ($ok, $msg) = manual_unlock($pin);
    print "$msg\n";
    exit($ok ? 0 : 1);
}
elsif ($action eq "--set-lock") {
    my $mode = $ARGV[1] || 0;
    my $pin = $ARGV[2] || "";
    my ($ok, $msg) = set_pin_lock($mode, $pin);
    print "$msg\n";
    exit($ok ? 0 : 1);
}
elsif ($action eq "--change-pin") {
    my $old = $ARGV[1] || "";
    my $new = $ARGV[2] || "";
    my ($ok, $msg) = change_pin($old, $new);
    print "$msg\n";
    exit($ok ? 0 : 1);
}
elsif ($action eq "--puk-unlock") {
    my $puk = $ARGV[1] || "";
    my $new = $ARGV[2] || "";
    my ($ok, $msg) = puk_unlock($puk, $new);
    print "$msg\n";
    exit($ok ? 0 : 1);
}
elsif ($action eq "--save-auto-unlock") {
    my $pin = $ARGV[1] || "";
    my ($ok, $msg) = save_auto_unlock($pin);
    print "$msg\n";
    exit($ok ? 0 : 1);
}
elsif ($action eq "--clear-auto-unlock") {
    my ($ok, $msg) = clear_auto_unlock();
    print "$msg\n";
    exit($ok ? 0 : 1);
}
else {
    print "Usage: sim_pin_helper.pl [--status | --auto-unlock | --unlock <PIN> | --set-lock <0|1> <PIN> | --change-pin <OLD> <NEW> | --puk-unlock <PUK> <NEW> | --save-auto-unlock <PIN> | --clear-auto-unlock]\n";
    exit(1);
}
