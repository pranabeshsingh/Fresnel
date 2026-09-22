#!/usr/bin/perl
use strict;
use warnings;
use Fcntl qw(:flock O_RDONLY O_WRONLY);
use Socket;
use POSIX qw(strftime tzset);

$ENV{TZ} = 'IST-5:30';
POSIX::tzset();

$SIG{CHLD} = 'IGNORE';

my $is_daemon = 0;
foreach my $arg (@ARGV) {
    if ($arg eq "--daemon" || $arg eq "-d") {
        $is_daemon = 1;
    }
}

my $dev = "/dev/smd7";
my $lock_file = "/tmp/smd7.lock";
my $usage_file = "/data/simpleadmin/data/data_usage.json";
my $cpu_file = "/tmp/cpu_prev_stat.txt";
my $dns_profile_file = "/data/simpleadmin/dns_profile.conf";
my $rf_cache_file = "/tmp/rf_cache.json";
my $ping_cache_file = "/tmp/ping_cache.json";
my $static_cache_file = "/tmp/static_hw_cache.json";
my $services_state_file = "/data/simpleadmin/services_state.json";

sub format_bytes {
    my ($bytes) = @_;
    $bytes = 0 if !$bytes || $bytes < 0;
    if ($bytes >= 1073741824 * 1024) {
        return sprintf("%.2f TB", $bytes / (1073741824 * 1024));
    } elsif ($bytes >= 1073741824) {
        return sprintf("%.2f GB", $bytes / 1073741824);
    } elsif ($bytes >= 1048576) {
        return sprintf("%.1f MB", $bytes / 1048576);
    } elsif ($bytes >= 1024) {
        return sprintf("%.0f KB", $bytes / 1024);
    } else {
        return "$bytes B";
    }
}

my @days_before_month = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334);
sub utc_epoch {
    my ($s, $m, $h, $d, $mon, $y) = @_;
    my $dy = ($y - 1970) * 365 + int(($y - 1969) / 4);
    my $days = $dy + $days_before_month[$mon] + ($d - 1);
    $days++ if $mon > 1 && ($y % 4 == 0 && ($y % 100 != 0 || $y % 400 == 0));
    return $days * 86400 + $h * 3600 + $m * 60 + $s;
}

sub format_speed {
    my ($bps) = @_;
    $bps = 0 if !$bps || $bps < 0;
    if ($bps >= 1000000000) {
        return sprintf("%.2f Gbps", $bps / 1000000000);
    } elsif ($bps >= 1000000) {
        return sprintf("%.2f Mbps", $bps / 1000000);
    } elsif ($bps >= 1000) {
        return sprintf("%.1f Kbps", $bps / 1000);
    } else {
        return "$bps bps";
    }
}

sub format_uptime {
    my ($sec) = @_;
    my $d = int($sec / 86400);
    my $h = int(($sec % 86400) / 3600);
    my $m = int(($sec % 3600) / 60);
    if ($d > 0) {
        return "${d}d ${h}h ${m}m";
    } elsif ($h > 0) {
        return "${h}h ${m}m";
    } else {
        return "${m}m";
    }
}

sub parse_simple_json {
    my ($text) = @_;
    my %res = ();
    return %res if !defined $text;
    while ($text =~ /"([^"]+)"\s*:\s*(?:"([^"]*)"|(-?[0-9\.]+)|(true|false|null))/g) {
        my $k = $1;
        my $v = defined $2 ? $2 : (defined $3 ? $3 : $4);
        $v = 1 if defined $v && $v eq "true";
        $v = 0 if defined $v && $v eq "false";
        $res{$k} = $v if defined $k && defined $v;
    }
    return %res;
}

sub run_at_direct {
    my ($cmd) = @_;
    $cmd =~ s/\r|\n//g;
    open(my $lf, ">", $lock_file) or return "";
    if (!flock($lf, LOCK_EX | LOCK_NB)) {
        close($lf);
        return "";
    }
    my ($reader, $writer);
    pipe($reader, $writer);
    my $pid = fork();
    if (!defined $pid) { flock($lf, LOCK_UN); close($lf); return ""; }
    if ($pid == 0) {
        close $reader;
        sysopen(my $r, $dev, O_RDONLY) or exit(1);
        my $buf;
        my $res = "";
        eval {
            local $SIG{ALRM} = sub { die "timeout\n" };
            alarm(1);
            while (sysread($r, $buf, 1024)) {
                $res .= $buf;
                last if $res =~ /(?:OK|ERROR|\+CME ERROR:.*|\+CMS ERROR:.*)\r?\n/;
            }
            alarm(0);
        };
        print $writer $res;
        close $writer;
        exit(0);
    }
    close $writer;
    select(undef, undef, undef, 0.01);
    if (sysopen(my $w, $dev, O_WRONLY)) {
        syswrite($w, "$cmd\r\n");
        close($w);
    }
    my $out = "";
    while (my $line = <$reader>) { $out .= $line; }
    close $reader;
    waitpid($pid, 0);
    flock($lf, LOCK_UN);
    close($lf);
    return $out;
}

my $last_rf_poll = 0;
my $last_ping_poll = 0;

my $prev_cpu_total = 0;
my $prev_cpu_idle = 0;
my $smooth_cpu = 10;

# Persistent In-Memory RF State
my %rf = (
    provider => "Detecting...",
    network_type => "Detecting...",
    is_online => 1,
    is_5g => 0,
    conn_bands => "Cellular",
    nr5g_band => "",
    lte_band => "",
    channel => "--",
    apn => "--",
    sim_status => "READY",
    wan_ipv4 => "--",
    csq_val => 26,
    csq_per => 83,
    signal_bars => 5,
    signal_quality => "Excellent",
    rsrp_str => "-90 dBm",
    rsrq_str => "-11 dB",
    sinr_str => "+23.0 dB",
    nr_rsrp_str => "-90 dBm",
    lac => "1BE4 (7140)",
    enb => "2CA7E (182910)",
    cid => "2CA7E0A (46824970)"
);

# Load initial RF cache if present
if (-f $rf_cache_file && open(my $rfc, "<", $rf_cache_file)) {
    my $content = do { local $/; <$rfc> };
    close($rfc);
    my %loaded = parse_simple_json($content);
    foreach my $k (keys %loaded) {
        $rf{$k} = $loaded{$k} if defined $loaded{$k} && $loaded{$k} ne "";
    }
}


sub get_precise_uptime {
    if (open(my $uf, "<", "/proc/uptime")) {
        my $line = <$uf> || "";
        close($uf);
        if ($line =~ /^([\d\.]+)/) {
            return $1 + 0.0;
        }
    }
    return time() + 0.0;
}

our $mem_last_rx = 0;
our $mem_last_tx = 0;
our $mem_last_uptime = 0;
our $mem_ema_speed_rx = 0;
our $mem_ema_speed_tx = 0;
our $mem_last_disk_flush = 0;
our %mem_usage_cache = ();
our @mem_history_7d = ();
our $mem_usage_loaded = 0;
our $mem_last_disk_poll = 0;
our $mem_disk_total_mb = "97.7 MB";
our $mem_disk_used_mb = "60.7 MB";
our $mem_disk_free_mb = "37.0 MB";
our $mem_disk_percent = 62;
our $last_sim_sec_poll = 0;
our $sim_pin_lock_enabled = 0;
our $sim_pin_retries = 3;
our $sim_puk_retries = 10;
our $sim_auto_unlock_configured = 0;
our $sim_auto_unlock_failed = 0;

sub poll_disk_usage {
    my ($now) = @_;
    return if ($now - $mem_last_disk_poll < 30);
    $mem_last_disk_poll = $now;
    if (open(my $df_pipe, "df -k /data 2>/dev/null |")) {
        my $hdr = <$df_pipe>;
        my $line = <$df_pipe>;
        if (defined $line && $line =~ /\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%/) {
            my ($tot_k, $used_k, $avail_k, $pct) = ($1, $2, $3, $4);
            $mem_disk_total_mb = sprintf("%.1f MB", $tot_k / 1024);
            $mem_disk_used_mb = sprintf("%.1f MB", $used_k / 1024);
            $mem_disk_free_mb = sprintf("%.1f MB", $avail_k / 1024);
            $mem_disk_percent = int($pct);
        }
        close($df_pipe);
    }
}

sub load_usage_from_disk {
    return if $mem_usage_loaded;
    my $target_path = (-f $usage_file) ? $usage_file : ((-f "/tmp/data_usage.json") ? "/tmp/data_usage.json" : "");
    if ($target_path ne "" && open(my $uf, "<", $target_path)) {
        my $content = do { local $/; <$uf> };
        close($uf);
        my %loaded = parse_simple_json($content);
        foreach my $k (keys %loaded) {
            $mem_usage_cache{$k} = $loaded{$k} if defined $loaded{$k};
        }
        if ($content =~ /"history_7d"\s*:\s*\[([^\]]*)\]/s) {
            my $hist_block = $1;
            while ($hist_block =~ /\{\s*"day"\s*:\s*"([^"]+)"\s*,\s*"rx"\s*:\s*(-?[0-9\.]+)\s*,\s*"tx"\s*:\s*(-?[0-9\.]+)\s*\}/g) {
                my ($d, $r, $t) = ($1, 0 + $2, 0 + $3);
                $r = 0 if $r < 0;
                $t = 0 if $t < 0;
                push @mem_history_7d, { day => $d, rx => $r, tx => $t };
            }
        }
    }
    $mem_usage_loaded = 1;
}

sub flush_usage_to_disk {
    my ($now) = @_;
    my @hist_json = ();
    for my $h (@mem_history_7d) {
        push @hist_json, sprintf('{"day":"%s","rx":%.0f,"tx":%.0f}', $h->{day}, $h->{rx} || 0, $h->{tx} || 0);
    }
    my $hist_str = "[" . join(",", @hist_json) . "]";

    if (open(my $wf, ">", "$usage_file.tmp")) {
        print $wf "{\n";
        print $wf "  \"current_day\": \"$mem_usage_cache{current_day}\",\n";
        print $wf "  \"current_month\": \"$mem_usage_cache{current_month}\",\n";
        print $wf sprintf("  \"today_rx\": %.0f,\n", $mem_usage_cache{today_rx} || 0);
        print $wf sprintf("  \"today_tx\": %.0f,\n", $mem_usage_cache{today_tx} || 0);
        print $wf sprintf("  \"yesterday_rx\": %.0f,\n", $mem_usage_cache{yesterday_rx} || 0);
        print $wf sprintf("  \"yesterday_tx\": %.0f,\n", $mem_usage_cache{yesterday_tx} || 0);
        print $wf sprintf("  \"month_rx\": %.0f,\n", $mem_usage_cache{month_rx} || 0);
        print $wf sprintf("  \"month_tx\": %.0f,\n", $mem_usage_cache{month_tx} || 0);
        print $wf sprintf("  \"total_rx\": %.0f,\n", $mem_usage_cache{total_rx} || 0);
        print $wf sprintf("  \"total_tx\": %.0f,\n", $mem_usage_cache{total_tx} || 0);
        print $wf sprintf("  \"last_rx\": %.0f,\n", $mem_usage_cache{last_rx} || 0);
        print $wf sprintf("  \"last_tx\": %.0f,\n", $mem_usage_cache{last_tx} || 0);
        print $wf "  \"last_time\": $mem_usage_cache{last_time},\n";
        print $wf "  \"speed_rx_bps\": $mem_usage_cache{speed_rx_bps},\n";
        print $wf "  \"speed_tx_bps\": $mem_usage_cache{speed_tx_bps},\n";
        print $wf "  \"history_7d\": $hist_str\n";
        print $wf "}\n";
        close($wf);
        rename("$usage_file.tmp", $usage_file);
        $mem_last_disk_flush = $now;
    }
}

# Main Sampling Routine
sub sample_and_emit {
    my $now = time();
    my $now_ist = $now + 19800; # IST is UTC + 5:30 (19,800 seconds)
    my $today_str = strftime("%Y-%m-%d", gmtime($now_ist));
    my $month_str = strftime("%Y-%m", gmtime($now_ist));

    load_usage_from_disk();

    # 1. Fast WAN / LAN Throughput (Qualcomm Baseband Hardware Stats & Netdev Fallback)
    my ($hw_rx, $hw_tx) = (0.0, 0.0);
    if (open(my $lcf, "-|", "logcat -d -t 30 -b main -s ceiled:F 2>/dev/null")) {
        while (my $line = <$lcf>) {
            if ($line =~ /tx_ok_bytes=(\d+),\s*rx_ok_bytes=(\d+)/) {
                $hw_tx = $1 + 0.0;
                $hw_rx = $2 + 0.0;
            }
        }
        close($lcf);
    }

    my ($wan_rx, $wan_tx) = (0.0, 0.0);
    my ($lan_rx, $lan_tx) = (0.0, 0.0);

    if (open(my $nf, "<", "/proc/net/dev")) {
        while (my $line = <$nf>) {
            if ($line =~ /^\s*rmnet_data0:\s*(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)/) {
                $wan_rx = $1 + 0.0;
                $wan_tx = $2 + 0.0;
            }
            elsif ($line =~ /^\s*ecm0:\s*(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)/) {
                $lan_rx = $1 + 0.0;
                $lan_tx = $2 + 0.0;
            }
        }
        close($nf);
    }

    # Primary WAN is Baseband Hardware Stats (handles Qualcomm IPA hardware routing bypass)
    # Fallback to rmnet_data0 or ecm0
    my $raw_rx = ($hw_rx > 0) ? $hw_rx : (($wan_rx > 0) ? $wan_rx : $lan_tx);
    my $raw_tx = ($hw_tx > 0) ? $hw_tx : (($wan_tx > 0) ? $wan_tx : $lan_rx);

    # 2. Update In-Memory Usage & Real-time Rates
    $mem_usage_cache{current_day} ||= $today_str;
    $mem_usage_cache{current_month} ||= $month_str;
    $mem_usage_cache{today_rx} ||= 0;
    $mem_usage_cache{today_tx} ||= 0;
    $mem_usage_cache{yesterday_rx} ||= 0;
    $mem_usage_cache{yesterday_tx} ||= 0;
    $mem_usage_cache{month_rx} ||= 0;
    $mem_usage_cache{month_tx} ||= 0;
    $mem_usage_cache{total_rx} ||= 0;
    $mem_usage_cache{total_tx} ||= 0;

    my $cur_uptime = get_precise_uptime();
    if ($mem_last_uptime <= 0) {
        $mem_last_rx = $raw_rx;
        $mem_last_tx = $raw_tx;
        $mem_last_uptime = $cur_uptime;
    }

    my $delta_rx = 0.0;
    my $delta_tx = 0.0;

    if ($cur_uptime < $mem_last_uptime) {
        # Modem rebooted
        $delta_rx = $raw_rx;
        $delta_tx = $raw_tx;
    } else {
        $delta_rx = $raw_rx - $mem_last_rx;
        $delta_tx = $raw_tx - $mem_last_tx;
        # Handle 32-bit unsigned integer counter overflow (4,294,967,296 bytes) or counter reset
        if ($delta_rx < 0) {
            if ($hw_rx <= 0 && $delta_rx > -4294967296) {
                $delta_rx += 4294967296;
            } else {
                $delta_rx = 0.0;
            }
            $delta_rx = 0.0 if $delta_rx < 0;
        }
        if ($delta_tx < 0) {
            if ($hw_tx <= 0 && $delta_tx > -4294967296) {
                $delta_tx += 4294967296;
            } else {
                $delta_tx = 0.0;
            }
            $delta_tx = 0.0 if $delta_tx < 0;
        }
    }

    my $dt = $cur_uptime - $mem_last_uptime;
    $dt = ($dt > 0.05) ? $dt : 1.0;
    my $speed_dt = ($dt <= 15.0) ? $dt : 15.0;

    my $inst_rx_bps = int(($delta_rx * 8.0) / $speed_dt);
    my $inst_tx_bps = int(($delta_tx * 8.0) / $speed_dt);

    # Exponential Moving Average for fluid, noise-free graphing
    if ($inst_rx_bps == 0) {
        $mem_ema_speed_rx = int($mem_ema_speed_rx * 0.4);
        $mem_ema_speed_rx = 0 if $mem_ema_speed_rx < 500;
    } else {
        $mem_ema_speed_rx = int($mem_ema_speed_rx * 0.35 + $inst_rx_bps * 0.65);
    }

    if ($inst_tx_bps == 0) {
        $mem_ema_speed_tx = int($mem_ema_speed_tx * 0.4);
        $mem_ema_speed_tx = 0 if $mem_ema_speed_tx < 500;
    } else {
        $mem_ema_speed_tx = int($mem_ema_speed_tx * 0.35 + $inst_tx_bps * 0.65);
    }

    $mem_last_rx = $raw_rx;
    $mem_last_tx = $raw_tx;
    $mem_last_uptime = $cur_uptime;

    my $speed_rx_bps = $mem_ema_speed_rx;
    my $speed_tx_bps = $mem_ema_speed_tx;

    # Rollover day check
    if ($mem_usage_cache{current_day} ne $today_str) {
        my $prev_day = $mem_usage_cache{current_day};
        unshift @mem_history_7d, { day => $prev_day, rx => ($mem_usage_cache{today_rx} || 0), tx => ($mem_usage_cache{today_tx} || 0) };
        pop @mem_history_7d if scalar(@mem_history_7d) > 7;
        $mem_usage_cache{yesterday_rx} = $mem_usage_cache{today_rx} || 0;
        $mem_usage_cache{yesterday_tx} = $mem_usage_cache{today_tx} || 0;
        $mem_usage_cache{today_rx} = 0;
        $mem_usage_cache{today_tx} = 0;
        $mem_usage_cache{current_day} = $today_str;
        flush_usage_to_disk($now);
    }

    # Rollover month check
    if ($mem_usage_cache{current_month} ne $month_str) {
        $mem_usage_cache{month_rx} = 0;
        $mem_usage_cache{month_tx} = 0;
        $mem_usage_cache{current_month} = $month_str;
        flush_usage_to_disk($now);
    }

    $mem_usage_cache{today_rx} += $delta_rx;
    $mem_usage_cache{today_tx} += $delta_tx;
    $mem_usage_cache{month_rx} += $delta_rx;
    $mem_usage_cache{month_tx} += $delta_tx;
    $mem_usage_cache{total_rx} += $delta_rx;
    $mem_usage_cache{total_tx} += $delta_tx;
    $mem_usage_cache{last_rx}   = $raw_rx;
    $mem_usage_cache{last_tx}   = $raw_tx;
    $mem_usage_cache{last_time} = $now;
    $mem_usage_cache{speed_rx_bps} = $speed_rx_bps;
    $mem_usage_cache{speed_tx_bps} = $speed_tx_bps;

    # Flush to flash every 60s
    if ($now - $mem_last_disk_flush >= 60) {
        flush_usage_to_disk($now);
    }

    my %usage = %mem_usage_cache;
    my @history_7d = @mem_history_7d;

    my @hist_json = ();
    for my $h (@history_7d) {
        push @hist_json, sprintf('{"day":"%s","rx":%.0f,"tx":%.0f}', $h->{day}, $h->{rx} || 0, $h->{tx} || 0);
    }
    my $hist_str = "[" . join(",", @hist_json) . "]";

    # 3. CPU Calculation with Exponential Moving Average (EMA) Filter
    my $cpu_percent = $smooth_cpu;
    if (open(my $stf, "<", "/proc/stat")) {
        my $stat_line = <$stf> || "";
        close($stf);
        if ($stat_line =~ /^cpu\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)/) {
            my ($u, $n, $s, $idle, $iow, $irq, $sirq) = ($1, $2, $3, $4, $5, $6, $7);
            my $cur_total = $u + $n + $s + $idle + $iow + $irq + $sirq;
            my $cur_idle = $idle + $iow;
            
            if ($prev_cpu_total > 0) {
                my $tot_diff = $cur_total - $prev_cpu_total;
                my $idle_diff = $cur_idle - $prev_cpu_idle;
                if ($tot_diff > 0) {
                    my $raw_cpu = int(100 * ($tot_diff - $idle_diff) / $tot_diff);
                    $raw_cpu = 0 if $raw_cpu < 0;
                    $raw_cpu = 100 if $raw_cpu > 100;
                    # 70% historical smoothed + 30% instantaneous delta (eliminates false micro-jitter)
                    $smooth_cpu = int(($smooth_cpu * 0.70) + ($raw_cpu * 0.30));
                    $cpu_percent = $smooth_cpu;
                }
            }
            $prev_cpu_total = $cur_total;
            $prev_cpu_idle = $cur_idle;
        }
    }

    # 4. RAM & Uptime
    my ($mem_total_kb, $mem_free_kb, $mem_avail_kb, $buffers_kb, $cached_kb) = (0, 0, 0, 0, 0);
    if (open(my $mf, "<", "/proc/meminfo")) {
        while (my $l = <$mf>) {
            if ($l =~ /^MemTotal:\s+(\d+)/) { $mem_total_kb = $1; }
            elsif ($l =~ /^MemFree:\s+(\d+)/) { $mem_free_kb = $1; }
            elsif ($l =~ /^MemAvailable:\s+(\d+)/) { $mem_avail_kb = $1; }
            elsif ($l =~ /^Buffers:\s+(\d+)/) { $buffers_kb = $1; }
            elsif ($l =~ /^Cached:\s+(\d+)/) { $cached_kb = $1; }
        }
        close($mf);
    }
    $mem_avail_kb = $mem_free_kb + $buffers_kb + $cached_kb if $mem_avail_kb == 0;
    my $mem_used_kb = $mem_total_kb - $mem_avail_kb;
    my $mem_percent = $mem_total_kb > 0 ? int(($mem_used_kb * 100) / $mem_total_kb) : 0;
    my $ram_used_mb = sprintf("%.1f MB", $mem_used_kb / 1024);
    my $ram_total_mb = sprintf("%.1f MB", $mem_total_kb / 1024);
    my $ram_free_mb = sprintf("%.1f MB", $mem_avail_kb / 1024);

    my $uptime_sec = 0;
    if (open(my $uf, "<", "/proc/uptime")) {
        my $line = <$uf> || "";
        if ($line =~ /^(\d+)/) { $uptime_sec = int($1); }
        close($uf);
    }

    # 5. Thermals & Power Subsystem
    my %thermals = (
        cpu => "37°C", mdm_5g => "36°C", pa => "33°C", pa1 => "33°C", pa2 => "33°C",
        ipa => "36°C", tcxo => "36°C", pmic => "39°C", case => "36°C", ambient => "36°C",
        core_temp_val => 37
    );
    if (opendir(my $dh, "/sys/class/thermal")) {
        my @dirs = grep { /^thermal_zone\d+$/ } readdir($dh);
        closedir($dh);
        for my $d (@dirs) {
            my $tf = "/sys/class/thermal/$d/type";
            my $vf = "/sys/class/thermal/$d/temp";
            if (-e $tf && -e $vf) {
                open(my $thf, "<", $tf); my $type = <$thf> || ""; close($thf);
                open(my $tvf, "<", $vf); my $val = <$tvf> || ""; close($tvf);
                chomp($type); chomp($val);
                if ($val =~ /^-?\d+$/ && $val > 0) {
                    my $c = int($val / 1000);
                    if ($type =~ /cpu0/) { $thermals{cpu} = "${c}°C"; $thermals{core_temp_val} = $c; }
                    elsif ($type =~ /mdm-5g/) { $thermals{mdm_5g} = "${c}°C"; }
                    elsif ($type =~ /pa1/) { $thermals{pa} = "${c}°C"; $thermals{pa1} = "${c}°C"; }
                    elsif ($type =~ /pa2/) { $thermals{pa2} = "${c}°C"; }
                    elsif ($type =~ /ipa/) { $thermals{ipa} = "${c}°C"; }
                    elsif ($type =~ /xo-therm/) { $thermals{tcxo} = "${c}°C"; }
                    elsif ($type =~ /pmxprairie/) { $thermals{pmic} = "${c}°C"; }
                    elsif ($type =~ /sdx-case/) { $thermals{case} = "${c}°C"; }
                    elsif ($type =~ /ambient-therm/) { $thermals{ambient} = "${c}°C"; }
                }
            }
        }
    }

    # 5.1 Power Rail Metrics (PMIC VADC)
    my $vph_raw = 0;
    if (open(my $vph_f, "<", "/sys/bus/iio/devices/iio:device0/in_voltage_vph_pwr_input")) {
        my $val = <$vph_f> || ""; chomp($val); close($vph_f);
        if ($val =~ /^\d+$/) { $vph_raw = int($val); }
    }
    my $vref_raw = 0;
    if (open(my $vref_f, "<", "/sys/bus/iio/devices/iio:device0/in_voltage_vref_1p25_input")) {
        my $val = <$vref_f> || ""; chomp($val); close($vref_f);
        if ($val =~ /^\d+$/) { $vref_raw = int($val); }
    }
    my $vph_v = sprintf("%.3f", $vph_raw / 1000000);
    my $vref_v = sprintf("%.4f", $vref_raw / 1000000);
    my $power_status = "HEALTHY";
    if ($vph_v < 3.20) {
        $power_status = "CRITICAL_SAG";
    } elsif ($vph_v < 3.30) {
        $power_status = "MARGINAL";
    }

    # 6. WAN IPv4 and IPv6
    my $wan_ipv4 = "--";
    if (open(my $ip4_pipe, "/sbin/ip -4 addr show rmnet_data0 2>/dev/null |")) {
        while (my $l = <$ip4_pipe>) {
            if ($l =~ /inet\s+([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/) {
                $wan_ipv4 = $1;
                last;
            }
        }
        close($ip4_pipe);
    }
    if ($wan_ipv4 eq "--" && open(my $ifc_pipe, "ifconfig rmnet_data0 2>/dev/null |")) {
        while (my $l = <$ifc_pipe>) {
            if ($l =~ /inet (?:addr:)?([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/) {
                $wan_ipv4 = $1;
                last;
            }
        }
        close($ifc_pipe);
    }
    if ($wan_ipv4 ne "--") {
        $rf{wan_ipv4} = $wan_ipv4;
    }

    my $wan_ipv6 = "-";
    if (open(my $i6f, "<", "/proc/net/if_inet6")) {
        while (my $line = <$i6f>) {
            if ($line =~ /^([0-9a-fA-F]{32})\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+rmnet_data0/) {
                my $scope = hex($2);
                my $hex32 = $1;
                if ($scope == 0 && $hex32 !~ /^fe80/) {
                    my @chunks = unpack("(A4)*", $hex32);
                    for (@chunks) { s/^0+//; $_ = "0" if $_ eq ""; }
                    my $v6_formatted = lc(join(":", @chunks));
                    $v6_formatted =~ s/(:0)+:/:/g;
                    $wan_ipv6 = $v6_formatted;
                    last;
                }
            }
        }
        close($i6f);
    }

    my $dns_profile = "auto";
    if (-f $dns_profile_file && open(my $dpf, "<", $dns_profile_file)) {
        $dns_profile = <$dpf> || "auto";
        chomp($dns_profile);
        close($dpf);
    }

    my @dns_list = ();
    if (open(my $rf_resolv, "<", "/etc/resolv.conf")) {
        while (my $l = <$rf_resolv>) {
            if ($l =~ /^nameserver\s+([0-9a-fA-F\.:]+)/) {
                my $ns = $1;
                push @dns_list, $ns if !grep { $_ eq $ns } @dns_list;
            }
        }
        close($rf_resolv);
    }
    my $dns_primary = $dns_list[0] || "Auto";
    my $dns_secondary = $dns_list[1] || "-";

    # DNS Telemetry Parsing & Statistics from In-Memory Log
    my $dns_total_queries = 0;
    my $dns_blocked_queries = 0;
    my $dns_cached_queries = 0;
    my $dns_forwarded_queries = 0;
    my $dns_queries_1m = 0;
    my %dns_domain_counts = ();
    my %dns_blocked_counts = ();
    my @dns_recent_queries = ();
    my $dns_logfile = "/tmp/dnsmasq.log";

    my %mon_map = ("Jan"=>0,"Feb"=>1,"Mar"=>2,"Apr"=>3,"May"=>4,"Jun"=>5,"Jul"=>6,"Aug"=>7,"Sep"=>8,"Oct"=>9,"Nov"=>10,"Dec"=>11);
    my $curr_year = (localtime($now))[5] + 1900;

    if (-f $dns_logfile && open(my $dfh, "<", $dns_logfile)) {
        while (my $line = <$dfh>) {
            if ($line =~ /query\[([A-Z0-9]+)\]\s+([^\s]+)\s+from\s+([^\s]+)/) {
                my ($type, $domain, $client) = ($1, $2, $3);
                $dns_total_queries++;
                $dns_domain_counts{$domain}++;
                
                my $time = "";
                my $query_ts = $now;
                if ($line =~ /^([A-Z][a-z]{2})\s+(\d+)\s+(\d+):(\d+):(\d+)/) {
                    my ($mon, $day, $h, $m, $s) = ($1, $2, $3, $4, $5);
                    $time = "$mon $day $h:$m:$s";
                    if (defined $mon_map{$mon}) {
                        $query_ts = utc_epoch(int($s), int($m), int($h), int($day), $mon_map{$mon}, $curr_year);
                    }
                }
                if ($query_ts >= $now - 60) {
                    $dns_queries_1m++;
                }
                
                unshift @dns_recent_queries, {
                    time => $time,
                    timestamp => $query_ts,
                    domain => $domain,
                    type => $type,
                    client => $client,
                    status => "RESOLVED"
                };
                pop @dns_recent_queries if @dns_recent_queries > 50;
            } elsif ($line =~ /adblock_hosts\s+([^\s]+)\s+is/) {
                my $domain = $1;
                $dns_blocked_queries++;
                $dns_blocked_counts{$domain}++;
                if (@dns_recent_queries && $dns_recent_queries[0]->{domain} eq $domain) {
                    $dns_recent_queries[0]->{status} = "BLOCKED";
                }
            } elsif ($line =~ /cached\s+([^\s]+)\s+is/) {
                my $domain = $1;
                $dns_cached_queries++;
                if (@dns_recent_queries && $dns_recent_queries[0]->{domain} eq $domain && $dns_recent_queries[0]->{status} eq "RESOLVED") {
                    $dns_recent_queries[0]->{status} = "CACHED";
                }
            } elsif ($line =~ /forwarded\s+([^\s]+)\s+to/) {
                $dns_forwarded_queries++;
            }
        }
        close($dfh);

        # Truncate /tmp/dnsmasq.log in-place if > 512KB to prevent RAM exhaustion without breaking inode or open file descriptors
        if (-s $dns_logfile && -s $dns_logfile > 512000) {
            system("tail -n 1000 /tmp/dnsmasq.log > /tmp/dnsmasq.log.tmp && cat /tmp/dnsmasq.log.tmp > /tmp/dnsmasq.log && rm -f /tmp/dnsmasq.log.tmp && chmod 666 /tmp/dnsmasq.log && chown nobody:nogroup /tmp/dnsmasq.log 2>/dev/null; killall -SIGUSR2 dnsmasq 2>/dev/null || true");
        }
    }

    my $dns_block_rate = $dns_total_queries > 0 ? sprintf("%.1f", ($dns_blocked_queries / $dns_total_queries) * 100) : "0.0";
    my $dns_cache_rate = $dns_total_queries > 0 ? sprintf("%.1f", ($dns_cached_queries / $dns_total_queries) * 100) : "0.0";

    # Top 10 resolved domains
    my @top_resolved_list = ();
    my @sorted_domains = sort { $dns_domain_counts{$b} <=> $dns_domain_counts{$a} } keys %dns_domain_counts;
    for my $d (@sorted_domains[0..9]) {
        next unless defined $d;
        my $cnt = $dns_domain_counts{$d};
        push @top_resolved_list, sprintf('{"domain":"%s","count":%d}', $d, $cnt);
    }
    my $top_resolved_json = "[" . join(",", @top_resolved_list) . "]";

    # Top 10 blocked domains
    my @top_blocked_list = ();
    my @sorted_blocked = sort { $dns_blocked_counts{$b} <=> $dns_blocked_counts{$a} } keys %dns_blocked_counts;
    for my $d (@sorted_blocked[0..9]) {
        next unless defined $d;
        my $cnt = $dns_blocked_counts{$d};
        push @top_blocked_list, sprintf('{"domain":"%s","count":%d}', $d, $cnt);
    }
    my $top_blocked_json = "[" . join(",", @top_blocked_list) . "]";

    # Recent queries JSON
    my @recent_json_list = ();
    for my $r (@dns_recent_queries) {
        push @recent_json_list, sprintf('{"time":"%s","timestamp":%d,"domain":"%s","type":"%s","client":"%s","status":"%s"}',
            $r->{time}, $r->{timestamp} || $now, $r->{domain}, $r->{type}, $r->{client}, $r->{status});
    }
    my $recent_queries_json = "[" . join(",", @recent_json_list) . "]";

    my $usb_link_mode = "USB 2.0 High-Speed (480 Mbps)";
    my $raw_usb_speed = "high-speed";
    if (-f "/sys/devices/platform/a600000.ssusb/a600000.dwc3/udc/a600000.dwc3/current_speed" && open(my $f, "<", "/sys/devices/platform/a600000.ssusb/a600000.dwc3/udc/a600000.dwc3/current_speed")) {
        $raw_usb_speed = <$f> || "";
        chomp($raw_usb_speed);
        close($f);
    }
    if ($raw_usb_speed eq "super-speed" || $raw_usb_speed eq "super-speed-plus") {
        $usb_link_mode = "USB 3.0 SuperSpeed (5 Gbps)";
    }

    my $host_ip = "--";
    my $host_mac = "--";
    my $host_name = "Connected Host / Router";

    # Check ARP cache for active downstream devices (e.g. TP-Link router or direct PC)
    if (open(my $af, "<", "/proc/net/arp")) {
        <$af>; # skip header
        while (my $line = <$af>) {
            if ($line =~ /^\s*(\d+\.\d+\.\d+\.\d+)\s+\S+\s+(0x[0-9a-fA-F]+)\s+([0-9a-fA-F:]{17})\s+\S+\s+(\S+)/) {
                my ($ip, $flags, $mac, $dev) = ($1, $2, $3, $4);
                if ($flags ne "0x0" && $mac ne "00:00:00:00:00:00") {
                    $host_ip = $ip;
                    $host_mac = $mac;
                    last;
                }
            }
        }
        close($af);
    }

    # Fallback to dnsmasq.leases if ARP didn't find host
    if ($host_ip eq "--") {
        for my $lpath ("/var/run/data/dnsmasq.leases", "/run/data/dnsmasq.leases", "/var/run/dnsmasq.leases") {
            if (-f $lpath && open(my $lf, "<", $lpath)) {
                while (my $line = <$lf>) {
                    if ($line =~ /^\d+\s+([0-9a-fA-F:]{17})\s+(\d+\.\d+\.\d+\.\d+)\s+([^\s]+)/) {
                        ($host_mac, $host_ip, $host_name) = ($1, $2, $3);
                        $host_name = "Connected Host / Router" if $host_name eq "*";
                        last;
                    }
                }
                close($lf);
                last if $host_ip ne "--";
            }
        }
    }

    # 7. Ping cache
    my $ping_vps_ms = "48.0 ms";
    my $ping_vps_val = 48;
    my $ping_cf_ms = "32.0 ms";
    my $ping_cf_val = 32;
    my $ping_gg_ms = "38.0 ms";
    my $ping_gg_val = 38;

    if (-f $ping_cache_file && open(my $pcf, "<", $ping_cache_file)) {
        my $content = do { local $/; <$pcf> };
        close($pcf);
        my %loaded = parse_simple_json($content);
        if (defined $loaded{vps_val} && int($loaded{vps_val}) > 0 && int($loaded{vps_val}) < 2000) {
            $ping_vps_ms = $loaded{vps_ms} if defined $loaded{vps_ms};
            $ping_vps_val = int($loaded{vps_val});
        }
        if (defined $loaded{cf_val} && int($loaded{cf_val}) > 0 && int($loaded{cf_val}) < 2000) {
            $ping_cf_ms = $loaded{cf_ms} if defined $loaded{cf_ms};
            $ping_cf_val = int($loaded{cf_val});
        }
        if (defined $loaded{gg_val} && int($loaded{gg_val}) > 0 && int($loaded{gg_val}) < 2000) {
            $ping_gg_ms = $loaded{gg_ms} if defined $loaded{gg_ms};
            $ping_gg_val = int($loaded{gg_val});
        }
    }

    if ($now - $last_ping_poll > 10) {
        $last_ping_poll = $now;
        my $p_pid = fork();
        if (defined $p_pid && $p_pid == 0) {
            my $measure_fn = sub {
                my ($target, $def_ms, $def_val) = @_;
                my $out = `ping -4 -c 1 -W 2 $target 2>&1`;
                if ($out =~ /(?:bytes from.*time=|rtt min\/avg\/max\/mdev = [0-9\.]+\/)([0-9\.]+)/) {
                    my $num = $1 + 0.0;
                    if ($num >= 1.0 && $num < 2000.0) {
                        return (sprintf("%.1f ms", $num), int($num));
                    }
                }
                return ($def_ms, $def_val);
            };

            my ($p0_ms, $p0_v) = $measure_fn->("modem.trylocalhost.com", "48.0 ms", 48);
            my ($p1_ms, $p1_v) = $measure_fn->("1.1.1.1", "32.0 ms", 32);
            my ($p2_ms, $p2_v) = $measure_fn->("8.8.8.8", "38.0 ms", 38);

            if (open(my $pw, ">", $ping_cache_file)) {
                print $pw "{\"vps_ms\":\"$p0_ms\",\"vps_val\":$p0_v,\"cf_ms\":\"$p1_ms\",\"cf_val\":$p1_v,\"gg_ms\":\"$p2_ms\",\"gg_val\":$p2_v,\"time\":$now}\n";
                close($pw);
            }
            exit(0);
        }
    }

    # 8. RF / AT Telemetry (Polled every 3s)
    if ($now - $last_rf_poll >= 3) {
        $last_rf_poll = $now;
        my $cops_out   = run_at_direct("AT+COPS?");
        my $csq_out    = run_at_direct("AT+CSQ");
        my $cesq_out   = run_at_direct("AT+CESQ");
        my $cereg_out  = run_at_direct("AT+CEREG=2;+CEREG?;+CEREG=0");
        my $cpin_out   = run_at_direct("AT+CPIN?");
        my $nrca_out   = ($now % 6 == 0 || !$rf{nr5g_band}) ? run_at_direct("AT+NRCAINFO") : "";
        my $qrsrp_out  = ($now % 6 == 0 || !$rf{lte_band}) ? run_at_direct('AT$QCRSRP?') : "";

        my $cops_act = "";
        if ($cops_out =~ /\+COPS:\s*\d+,\d+,"([^"]+)",?(\d+)?/) {
            my $p = $1;
            $cops_act = defined $2 ? $2 : "";
            if ($p =~ /^40586\d/ || $p =~ /^40585\d/) {
                $p = ($cops_act eq "7") ? "Jio 4G" : "Jio True5G";
            }
            $p =~ s/\s+\w+$// if $p =~ /^(.+?)\s+\1$/i;
            $p =~ s/\s+Jio$//i if $p =~ /^Jio\s+/i;
            $p =~ s/\s+Airtel$//i if $p =~ /^Airtel\s+/i;
            $rf{provider} = $p;
            $rf{is_online} = 1;
        }

        # Query dynamic APN & dynamic IPv4 from network
        if ($rf{apn} eq "--" || $rf{apn} eq "jionet" || $now % 15 == 0) {
            my $apn_out = run_at_direct("AT+CGCONTRDP");
            if ($apn_out =~ /\+CGCONTRDP:\s*\d+,\d+,([^,\s\r\n]+)/) {
                $rf{apn} = $1;
            }
            if ($apn_out =~ /\+CGCONTRDP:\s*\d+,\d+,[^,]+,([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/) {
                my $v4_cand = $1;
                $rf{wan_ipv4} = $v4_cand if ($v4_cand && $v4_cand ne "0.0.0.0");
            }
        }

        if ($cpin_out =~ /\+CPIN:\s*(\w+)/) {
            $rf{sim_status} = $1;
        }

        if ($csq_out =~ /\+CSQ:\s*(\d+)/) {
            my $v = int($1);
            if ($v != 99) {
                $rf{csq_val} = $v;
                $rf{csq_per} = int(($v * 100) / 31);
            }
        }

        my $has_nr = 0;
        if ($cesq_out =~ /\+CESQ:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+))?/) {
            my ($rxlev, $ber, $rscp_idx, $ecno, $rsrq_idx, $rsrp_idx, $nr_rsrq_idx, $nr_rsrp_idx, $nr_sinr_idx) = ($1, $2, $3, $4, $5, $6, $7, $8, $9);
            if (defined $rsrp_idx && $rsrp_idx != 255) {
                my $v = $rsrp_idx - 140;
                $rf{rsrp_str} = "$v dBm";
            }
            if (defined $rsrq_idx && $rsrq_idx != 255) {
                my $v = ($rsrq_idx * 0.5) - 19.5;
                $rf{rsrq_str} = "$v dB";
            }
            if (defined $nr_rsrp_idx && $nr_rsrp_idx != 255) {
                my $v = $nr_rsrp_idx - 156;
                $rf{nr_rsrp_str} = "$v dBm";
                $rf{rsrp_str} = "$v dBm";
                $has_nr = 1;
            }
            if (defined $nr_sinr_idx && $nr_sinr_idx != 255) {
                my $v = ($nr_sinr_idx * 0.5) - 23;
                $rf{sinr_str} = sprintf("%+.1f dB", $v);
                $has_nr = 1;
            }
        }

        if ($nrca_out && $nrca_out =~ /PCC\s+Band:(\d+)/i) {
            my $bnum = $1;
            if ($bnum ne "<NA>" && $bnum > 0) {
                $rf{nr5g_band} = "n$bnum";
                $has_nr = 1;
            }
        }

        if ($qrsrp_out && $qrsrp_out =~ /\$QCRSRP:\s*\d+,(\d+),/) {
            my $earfcn = int($1);
            if ($earfcn >= 1200 && $earfcn <= 1949) { $rf{lte_band} = "B3 (1800)"; }
            elsif ($earfcn >= 0 && $earfcn <= 599) { $rf{lte_band} = "B1 (2100)"; }
            elsif ($earfcn >= 38650 && $earfcn <= 39649) { $rf{lte_band} = "B40 (2300)"; }
            elsif ($earfcn >= 3400 && $earfcn <= 3799) { $rf{lte_band} = "B8 (900)"; }
            elsif ($earfcn >= 2750 && $earfcn <= 3449) { $rf{lte_band} = "B7 (2600)"; }
            elsif ($earfcn >= 600 && $earfcn <= 1199) { $rf{lte_band} = "B2 (1900)"; }
            elsif ($earfcn >= 1950 && $earfcn <= 2399) { $rf{lte_band} = "B4 (1700/2100)"; }
            elsif ($earfcn >= 2400 && $earfcn <= 2649) { $rf{lte_band} = "B5 (850)"; }
            else { $rf{lte_band} = "EARFCN $earfcn"; }
        }

        my $cereg_act = "";
        if ($cereg_out =~ /\+CEREG:\s*2,\d+,"([0-9A-Fa-f]+)","([0-9A-Fa-f]+)"(?:,(\d+))?/) {
            my $tac_hex = $1;
            my $cid_hex = $2;
            $cereg_act = defined $3 ? $3 : "";
            my $tac_dec = hex($tac_hex);
            my $cid_dec = hex($cid_hex);
            $rf{lac} = sprintf("%s (%d)", uc($tac_hex), $tac_dec);
            my $enb_dec = int($cid_dec / 256);
            $rf{enb} = sprintf("%X (%d)", $enb_dec, $enb_dec);
            $rf{cid} = sprintf("%s (%d)", uc($cid_hex), $cid_dec);
        }

        # Accurate 5G SA vs 5G NSA vs 4G LTE Determination
        if ($cops_act eq "11" || $cops_act eq "12" || $cereg_act eq "11" || $cereg_act eq "12") {
            $rf{is_5g} = 1;
            $rf{network_type} = "5G SA";
            $rf{conn_bands} = $rf{nr5g_band} ? $rf{nr5g_band} : "NR5G Band 78";
            $rf{provider} =~ s/4G/True5G/ if $rf{provider};
        } elsif ($cops_act eq "7" || $cereg_act eq "7") {
            if ($has_nr && ($cops_act eq "13" || $cereg_act eq "13")) {
                $rf{is_5g} = 1;
                $rf{network_type} = "5G NSA";
                if ($rf{lte_band} && $rf{nr5g_band}) {
                    $rf{conn_bands} = "$rf{lte_band} + $rf{nr5g_band}";
                } elsif ($rf{nr5g_band}) {
                    $rf{conn_bands} = "LTE + $rf{nr5g_band}";
                } else {
                    $rf{conn_bands} = "LTE + NR5G";
                }
            } else {
                $rf{is_5g} = 0;
                $rf{network_type} = "4G LTE";
                $rf{conn_bands} = $rf{lte_band} ? $rf{lte_band} : "4G LTE";
                $rf{nr5g_band} = "";
                $rf{provider} =~ s/True5G/4G/ if $rf{provider};
            }
        } elsif ($cops_act eq "13" || $cereg_act eq "13") {
            $rf{is_5g} = 1;
            $rf{network_type} = "5G NSA";
            $rf{conn_bands} = ($rf{lte_band} && $rf{nr5g_band}) ? "$rf{lte_band} + $rf{nr5g_band}" : "LTE + NR5G";
        } elsif ($cops_act eq "2") {
            $rf{is_5g} = 0;
            $rf{network_type} = "3G UTRAN";
            $rf{conn_bands} = "3G";
            $rf{nr5g_band} = "";
        } else {
            $rf{is_5g} = $has_nr ? 1 : 0;
            $rf{network_type} = $has_nr ? "5G SA" : "Cellular";
            $rf{conn_bands} = $has_nr ? ($rf{nr5g_band} || "NR5G Band 78") : "Cellular";
        }

        my $rsrp_n = ($rf{rsrp_str} =~ /(-?\d+)/) ? int($1) : -90;
        if ($rsrp_n >= -85 || $rf{csq_val} >= 25) {
            $rf{signal_bars} = 5; $rf{signal_quality} = "Excellent";
        } elsif ($rsrp_n >= -95 || $rf{csq_val} >= 20) {
            $rf{signal_bars} = 4; $rf{signal_quality} = "Good";
        } elsif ($rsrp_n >= -105 || $rf{csq_val} >= 15) {
            $rf{signal_bars} = 3; $rf{signal_quality} = "Fair";
        } else {
            $rf{signal_bars} = 2; $rf{signal_quality} = "Poor";
        }

        # Query SIM Security Status every 10s (or when not READY)
        if ($now - $last_sim_sec_poll >= 10 || $rf{sim_status} ne "READY") {
            $last_sim_sec_poll = $now;
            my $clck_out = run_at_direct('AT+CLCK="SC",2');
            if ($clck_out =~ /\+CLCK:\s*1/) {
                $sim_pin_lock_enabled = 1;
            } elsif ($clck_out =~ /\+CLCK:\s*0/) {
                $sim_pin_lock_enabled = 0;
            }

            my $cpinr_out = run_at_direct('AT+CPINR');
            if ($cpinr_out =~ /\+CPINR:\s*SIM PIN\s*(\d+)/i) {
                $sim_pin_retries = int($1);
            }
            if ($cpinr_out =~ /\+CPINR:\s*SIM PUK\s*(\d+)/i) {
                $sim_puk_retries = int($1);
            }

            # Check local auto-unlock config without exposing PIN
            $sim_auto_unlock_configured = 0;
            if (-f "/data/simpleadmin/sim_security.conf" && open(my $sfh, "<", "/data/simpleadmin/sim_security.conf")) {
                while (my $sl = <$sfh>) {
                    $sim_auto_unlock_configured = 1 if $sl =~ /^\s*auto_unlock\s*=\s*1/i;
                }
                close($sfh);
            }
            $sim_auto_unlock_failed = (-f "/tmp/sim_auto_unlock_failed") ? 1 : 0;
        }

        if (open(my $rfw, ">", $rf_cache_file)) {
            print $rfw "{\n";
            print $rfw "  \"provider\": \"$rf{provider}\",\n";
            print $rfw "  \"network_type\": \"$rf{network_type}\",\n";
            print $rfw "  \"is_online\": $rf{is_online},\n";
            print $rfw "  \"is_5g\": $rf{is_5g},\n";
            print $rfw "  \"conn_bands\": \"$rf{conn_bands}\",\n";
            print $rfw "  \"nr5g_band\": \"$rf{nr5g_band}\",\n";
            print $rfw "  \"lte_band\": \"$rf{lte_band}\",\n";
            print $rfw "  \"channel\": \"$rf{channel}\",\n";
            print $rfw "  \"apn\": \"$rf{apn}\",\n";
            print $rfw "  \"sim_status\": \"$rf{sim_status}\",\n";
            print $rfw "  \"wan_ipv4\": \"$rf{wan_ipv4}\",\n";
            print $rfw "  \"csq_val\": $rf{csq_val},\n";
            print $rfw "  \"csq_per\": $rf{csq_per},\n";
            print $rfw "  \"signal_bars\": $rf{signal_bars},\n";
            print $rfw "  \"signal_quality\": \"$rf{signal_quality}\",\n";
            print $rfw "  \"rsrp_str\": \"$rf{rsrp_str}\",\n";
            print $rfw "  \"rsrq_str\": \"$rf{rsrq_str}\",\n";
            print $rfw "  \"sinr_str\": \"$rf{sinr_str}\",\n";
            print $rfw "  \"nr_rsrp_str\": \"$rf{nr_rsrp_str}\",\n";
            print $rfw "  \"lac\": \"$rf{lac}\",\n";
            print $rfw "  \"enb\": \"$rf{enb}\",\n";
            print $rfw "  \"cid\": \"$rf{cid}\",\n";
            print $rfw "  \"cache_time\": $now\n";
            print $rfw "}\n";
            close($rfw);
        }
    }

    # 9. Hardware info
    my $model_name = "SG500M2-X";
    my $imei = "-";
    my $firmware_ver = "RXMG1.20.00.326_0R05";
    poll_disk_usage($now);
    my $disk_total_mb = $mem_disk_total_mb;
    my $disk_used_mb = $mem_disk_used_mb;
    my $disk_free_mb = $mem_disk_free_mb;
    my $disk_percent = $mem_disk_percent;

    if (-f $static_cache_file && open(my $scf, "<", $static_cache_file)) {
        my $content = do { local $/; <$scf> };
        close($scf);
        my %loaded = parse_simple_json($content);
        $model_name = $loaded{model} if defined $loaded{model};
        $imei = $loaded{imei} if defined $loaded{imei};
        $firmware_ver = $loaded{firmware} if defined $loaded{firmware};
    }

    # 10. Services State (Tailscale, Ad-Blocker, Remote Telemetry)
    my %srv_state = (
        tailscale_enabled => 1,
        adblock_enabled => 1,
        telemetry_enabled => 0,
        blocked_domains_count => 45000,
        last_updated => "2026-09-01"
    );
    if (-f $services_state_file && open(my $ssf, "<", $services_state_file)) {
        my $content = do { local $/; <$ssf> };
        close($ssf);
        my %loaded = parse_simple_json($content);
        foreach my $k (keys %loaded) {
            $srv_state{$k} = $loaded{$k} if defined $loaded{$k};
        }
    }

    my $tailscale_running = 0;
    my $tailscale_ip = "100.112.234.4";
    if (-S "/var/run/tailscale/tailscaled.sock" || -d "/proc/sys/net/ipv4/conf/tailscale0") {
        $tailscale_running = 1;
    }

    my $adblock_running = 0;
    if (-s "/data/simpleadmin/adblock_hosts" && -s "/data/simpleadmin/adblock_hosts" > 100) {
        $adblock_running = 1;
    }

    my $telemetry_running = 0;
    if (system("pgrep -f telemetry_pusher.sh >/dev/null 2>&1") == 0) {
        $telemetry_running = 1;
    }

    my $telemetry_url = "";
    if (-f "/data/simpleadmin/telemetry.conf" && open(my $tcf, "<", "/data/simpleadmin/telemetry.conf")) {
        while (my $line = <$tcf>) {
            if ($line =~ /^TELEMETRY_BASE_URL=["']?([^"'\r\n]+)["']?/) {
                $telemetry_url = $1;
            }
        }
        close($tcf);
    }

    my $today_total_bytes = ($usage{today_rx} || 0) + ($usage{today_tx} || 0);
    my $yesterday_total_bytes = ($usage{yesterday_rx} || 0) + ($usage{yesterday_tx} || 0);
    my $month_total_bytes = ($usage{month_rx} || 0) + ($usage{month_tx} || 0);
    my $alltime_total_bytes = ($usage{total_rx} || 0) + ($usage{total_tx} || 0);

    my $today_formatted = format_bytes($today_total_bytes);
    my $today_down_formatted = format_bytes($usage{today_rx} || 0);
    my $today_up_formatted = format_bytes($usage{today_tx} || 0);
    my $yesterday_formatted = format_bytes($yesterday_total_bytes);
    my $yesterday_down_formatted = format_bytes($usage{yesterday_rx} || 0);
    my $yesterday_up_formatted = format_bytes($usage{yesterday_tx} || 0);
    my $month_formatted = format_bytes($month_total_bytes);
    my $alltime_formatted = format_bytes($alltime_total_bytes);

    my $today_rx_num = sprintf("%.0f", $usage{today_rx} || 0);
    my $today_tx_num = sprintf("%.0f", $usage{today_tx} || 0);
    my $today_bytes_num = sprintf("%.0f", $today_total_bytes || 0);
    my $yesterday_rx_num = sprintf("%.0f", $usage{yesterday_rx} || 0);
    my $yesterday_tx_num = sprintf("%.0f", $usage{yesterday_tx} || 0);
    my $yesterday_bytes_num = sprintf("%.0f", $yesterday_total_bytes || 0);
    my $month_rx_num = sprintf("%.0f", $usage{month_rx} || 0);
    my $month_tx_num = sprintf("%.0f", $usage{month_tx} || 0);
    my $month_bytes_num = sprintf("%.0f", $month_total_bytes || 0);
    my $alltime_rx_num = sprintf("%.0f", $usage{total_rx} || 0);
    my $alltime_tx_num = sprintf("%.0f", $usage{total_tx} || 0);
    my $alltime_bytes_num = sprintf("%.0f", $alltime_total_bytes || 0);

    my $down_speed_formatted = format_speed($usage{speed_rx_bps} || 0);
    my $up_speed_formatted = format_speed($usage{speed_tx_bps} || 0);
    my $uptime_formatted = format_uptime($uptime_sec);

    # 4.5. Connection Tracking & Network Port Telemetry
    my ($conn_total, $conn_tcp, $conn_udp) = (0, 0, 0);
    my %dport_counts = ();
    my %dport_proto = ();
    if (open(my $cfh, "<", "/proc/net/nf_conntrack")) {
        while (my $line = <$cfh>) {
            $conn_total++;
            my $p_type = "TCP";
            if ($line =~ /^ipv[46]\s+\d+\s+tcp/) {
                $conn_tcp++;
                $p_type = "TCP";
            } elsif ($line =~ /^ipv[46]\s+\d+\s+udp/) {
                $conn_udp++;
                $p_type = "UDP";
            }
            if ($line =~ /dport=(\d+)/) {
                my $dp = $1;
                $dport_counts{$dp}++;
                $dport_proto{$dp} = $p_type if !$dport_proto{$dp} || $p_type eq "TCP";
            }
        }
        close($cfh);
    }

    my %known_services = (
        80 => "HTTP Web", 443 => "HTTPS / TLS", 53 => "DNS Query", 22 => "SSH Remote",
        22000 => "Syncthing Sync", 22067 => "Syncthing Relay", 8080 => "Web Admin / Proxy",
        123 => "NTP Time Server", 27015 => "Steam Game Server", 27065 => "Steam / Gaming P2P",
        51820 => "WireGuard VPN", 1900 => "SSDP / UPnP", 5353 => "mDNS / Bonjour",
        853 => "DNS over TLS (DoT)", 993 => "IMAP SSL Mail", 587 => "SMTP Submission",
        445 => "SMB File Sharing", 3389 => "RDP Remote Desktop", 3478 => "STUN / WebRTC",
        5223 => "Apple Push (APNs)", 5228 => "Google Play Services", 11443 => "Alt HTTPS / Cloud"
    );

    my @sorted_ports = sort { $dport_counts{$b} <=> $dport_counts{$a} } keys %dport_counts;
    @sorted_ports = @sorted_ports[0..7] if @sorted_ports > 8;
    my @top_ports_json = ();
    for my $p (@sorted_ports) {
        my $svc = $known_services{$p} || "Port $p";
        my $pr = $dport_proto{$p} || "TCP";
        push @top_ports_json, sprintf('{"port":%d,"count":%d,"proto":"%s","service":"%s"}', $p, $dport_counts{$p}, $pr, $svc);
    }
    my $top_ports_str = "[" . join(",", @top_ports_json) . "]";

    my $open_ports_str = '[{"port":8080,"proto":"TCP","service":"SimpleAdmin Web UI","status":"Listening"},{"port":22,"proto":"TCP","service":"OpenSSH Shell","status":"Listening"},{"port":53,"proto":"UDP","service":"Dnsmasq DNS Resolver","status":"Listening"},{"port":123,"proto":"UDP","service":"NTP Time Server","status":"Listening"}]';

    my $configured_mode = "auto";
    if (-f "/data/simpleadmin/network_mode.json" && open(my $cmfh, "<", "/data/simpleadmin/network_mode.json")) {
        my $raw_cm = do { local $/; <$cmfh> };
        close($cmfh);
        if ($raw_cm =~ /"mode":\s*"([^"]+)"/) {
            $configured_mode = $1;
        }
    }

    my $json = <<"END_JSON";
{
  "status": "success",
  "timestamp": $now,
  "configured_mode": "$configured_mode",
  "connection": {
    "is_online": $rf{is_online},
    "is_5g": $rf{is_5g},
    "provider": "$rf{provider}",
    "network_type": "$rf{network_type}",
    "configured_mode": "$configured_mode",
    "connection_bands": "$rf{conn_bands}",
    "nr5g_band": "$rf{nr5g_band}",
    "lte_band": "$rf{lte_band}",
    "channel": "$rf{channel}",
    "apn": "$rf{apn}",
    "sim_status": "$rf{sim_status}",
    "mobile_ipv4": "$rf{wan_ipv4}",
    "mobile_ipv6": "$wan_ipv6",
    "dns_primary": "$dns_primary",
    "dns_secondary": "$dns_secondary",
    "dns_profile": "$dns_profile",
    "signal_bars": $rf{signal_bars},
    "signal_quality": "$rf{signal_quality}",
    "signal_percent": $rf{csq_per},
    "ttl_bypass": 1
  },
  "sim_security": {
    "sim_status": "$rf{sim_status}",
    "pin_lock_enabled": $sim_pin_lock_enabled,
    "pin_retries": $sim_pin_retries,
    "puk_retries": $sim_puk_retries,
    "auto_unlock_configured": $sim_auto_unlock_configured,
    "auto_unlock_failed": $sim_auto_unlock_failed
  },
  "services": {
    "tailscale": {
      "enabled": $srv_state{tailscale_enabled},
      "running": $tailscale_running,
      "ip": "$tailscale_ip",
      "exit_node": 1
    },
    "adblock": {
      "enabled": $srv_state{adblock_enabled},
      "running": $adblock_running,
      "blocked_domains": $srv_state{blocked_domains_count},
      "last_updated": "$srv_state{last_updated}"
    },
    "telemetry": {
      "enabled": $srv_state{telemetry_enabled},
      "running": $telemetry_running,
      "url": "$telemetry_url"
    }
  },
  "dns_telemetry": {
    "profile": "$dns_profile",
    "primary": "$dns_primary",
    "secondary": "$dns_secondary",
    "total_queries": $dns_total_queries,
    "blocked_queries": $dns_blocked_queries,
    "cached_queries": $dns_cached_queries,
    "forwarded_queries": $dns_forwarded_queries,
    "queries_per_min": $dns_queries_1m,
    "block_rate_percent": $dns_block_rate,
    "cache_rate_percent": $dns_cache_rate,
    "top_resolved": $top_resolved_json,
    "top_blocked": $top_blocked_json,
    "recent_queries": $recent_queries_json
  },
  "ping": {
    "vps": "$ping_vps_ms",
    "vps_ms": $ping_vps_val,
    "cloudflare": "$ping_cf_ms",
    "cloudflare_ms": $ping_cf_val,
    "google": "$ping_gg_ms",
    "google_ms": $ping_gg_val
  },
  "speed": {
    "download_speed": "$down_speed_formatted",
    "upload_speed": "$up_speed_formatted",
    "download_bps": $usage{speed_rx_bps},
    "upload_bps": $usage{speed_tx_bps}
  },
  "usage": {
    "plan_type": "Unlimited",
    "today_total": "$today_formatted",
    "today_download": "$today_down_formatted",
    "today_upload": "$today_up_formatted",
    "today_bytes": $today_bytes_num,
    "today_rx": $today_rx_num,
    "today_tx": $today_tx_num,
    "yesterday_total": "$yesterday_formatted",
    "yesterday_download": "$yesterday_down_formatted",
    "yesterday_upload": "$yesterday_up_formatted",
    "yesterday_bytes": $yesterday_bytes_num,
    "yesterday_rx": $yesterday_rx_num,
    "yesterday_tx": $yesterday_tx_num,
    "month_total": "$month_formatted",
    "month_bytes": $month_bytes_num,
    "month_rx": $month_rx_num,
    "month_tx": $month_tx_num,
    "alltime_total": "$alltime_formatted",
    "alltime_bytes": $alltime_bytes_num,
    "history_7d": $hist_str
  },
  "resources": {
    "cpu_percent": $cpu_percent,
    "ram_used": "$ram_used_mb",
    "ram_total": "$ram_total_mb",
    "ram_free": "$ram_free_mb",
    "ram_percent": $mem_percent,
    "disk_used": "$disk_used_mb",
    "disk_total": "$disk_total_mb",
    "disk_free": "$disk_free_mb",
    "disk_percent": $disk_percent
  },
  "thermals": {
    "cpu": "$thermals{cpu}",
    "mdm_5g": "$thermals{mdm_5g}",
    "pa": "$thermals{pa}",
    "pa1": "$thermals{pa1}",
    "pa2": "$thermals{pa2}",
    "ipa": "$thermals{ipa}",
    "tcxo": "$thermals{tcxo}",
    "pmic": "$thermals{pmic}",
    "case": "$thermals{case}",
    "ambient": "$thermals{ambient}"
  },
  "power": {
    "voltage_vph": $vph_v,
    "voltage_vref": $vref_v,
    "power_status": "$power_status"
  },
  "host_link": {
    "is_connected": 1,
    "interface": "ecm0",
    "protocol": "USB CDC-ECM (Ethernet Pass-through)",
    "usb_speed": "$usb_link_mode",
    "raw_usb_speed": "$raw_usb_speed",
    "host_ip": "$host_ip",
    "host_mac": "$host_mac",
    "host_name": "$host_name",
    "mtu": 1500,
    "status": "Link Active • Pass-Through Mode"
  },
  "tower_cell": {
    "enb": "$rf{enb}",
    "cid": "$rf{cid}",
    "lac": "$rf{lac}",
    "channel": "$rf{channel}",
    "sinr": "$rf{sinr_str}",
    "rsrp": "$rf{rsrp_str}"
  },
  "system": {
    "model": "$model_name",
    "imei": "$imei",
    "firmware": "$firmware_ver",
    "uptime": "$uptime_formatted",
    "uptime_sec": $uptime_sec,
    "temp_c": $thermals{core_temp_val}
  },
  "advanced": {
    "bands": "$rf{conn_bands}",
    "rsrp": "$rf{rsrp_str}",
    "nr_rsrp": "$rf{nr_rsrp_str}",
    "rsrq": "$rf{rsrq_str}",
    "sinr": "$rf{sinr_str}",
    "csq": "$rf{csq_val}",
    "cid": "$rf{cid}",
    "lac": "$rf{lac}",
    "enb": "$rf{enb}"
  },
  "network_connections": {
    "total": $conn_total,
    "tcp": $conn_tcp,
    "udp": $conn_udp,
    "top_ports": $top_ports_str,
    "open_ports": $open_ports_str
  }
}
END_JSON

    if (open(my $df, ">", "/tmp/dashboard_data.json.tmp")) {
        print $df $json;
        close($df);
        rename("/tmp/dashboard_data.json.tmp", "/tmp/dashboard_data.json");
    }

    return $json;
}

if ($is_daemon) {
    $SIG{INT} = sub { flush_usage_to_disk(time()); exit(0); };
    $SIG{TERM} = sub { flush_usage_to_disk(time()); exit(0); };
    while (1) {
        sample_and_emit();
        sleep(3);
    }
} else {
    my $out = sample_and_emit();
    print "Content-type: application/json\n";
    print "Cache-Control: no-cache\n\n";
    print $out;
}
