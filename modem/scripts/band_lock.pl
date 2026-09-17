#!/usr/bin/perl
use strict;
use warnings;
use Fcntl qw(:DEFAULT :flock);

my $dev = "/dev/smd7";
my $session_dir = "/tmp/gw_sessions";
my $lock_file = "/tmp/smd7.lock";

my $query_str = $ENV{QUERY_STRING} || "";
my $post_data = "";
if (($ENV{REQUEST_METHOD} || "") eq "POST") {
    my $len = $ENV{CONTENT_LENGTH} || 0;
    read(STDIN, $post_data, $len) if $len > 0;
}

my $params_str = $post_data ? "$query_str&$post_data" : $query_str;
$params_str =~ s/;//g;

my %params = ();
for my $pair (split(/&/, $params_str)) {
    my ($k, $v) = split(/=/, $pair, 2);
    next if !defined $k;
    $v = "" if !defined $v;
    $v =~ s/\+/ /g;
    $v =~ s/%([0-9a-fA-F]{2})/chr(hex($1))/eg;
    $params{$k} = $v;
}

my $token = $params{token} || "";
my $action = $params{action} || "get_mode";
my $mode = $params{mode} || "";

print "Content-type: application/json\n";
print "Cache-Control: no-cache\n\n";

if (!$token || ! -f "$session_dir/$token") {
    print "{\"status\":\"error\",\"message\":\"Unauthorized\"}\n";
    exit(0);
}

sub run_at {
    my ($cmd) = @_;
    $cmd =~ s/\r|\n//g;
    
    open(my $lf, '>>', $lock_file);
    flock($lf, LOCK_EX);

    my ($reader, $writer);
    pipe($reader, $writer);
    my $pid = fork();
    if (!defined $pid) { 
        flock($lf, LOCK_UN);
        close($lf);
        return ""; 
    }
    if ($pid == 0) {
        close $reader;
        sysopen(my $r, $dev, O_RDONLY) or exit(1);
        my $buf;
        my $res = "";
        eval {
            local $SIG{ALRM} = sub { die "timeout\n" };
            alarm(3);
            while (sysread($r, $buf, 1024)) {
                $res .= $buf;
                last if $res =~ /(?:OK|ERROR|\+CMS ERROR:.*|\+CME ERROR:.*)\r?\n/;
            }
            alarm(0);
        };
        print $writer $res;
        close $writer;
        exit(0);
    }
    close $writer;
    select(undef, undef, undef, 0.03);
    if (sysopen(my $w, $dev, O_WRONLY)) {
        syswrite($w, "$cmd\r\n");
        close($w);
    }
    my $out = "";
    while (my $line = <$reader>) {
        $out .= $line;
    }
    close $reader;
    waitpid($pid, 0);

    flock($lf, LOCK_UN);
    close($lf);
    return $out;
}

if ($action eq "set_mode") {
    my $ws46_val = 25;
    my $preset_name = "Auto (5G/4G)";

    if ($mode eq "5g_only" || $mode eq "5g_n78" || $mode eq "5g_n28") {
        $ws46_val = 30; # 5G NR Only / SA Lock
        $preset_name = "5G Only Standalone";
    } elsif ($mode eq "4g_only") {
        $ws46_val = 28; # 4G LTE Only
        $preset_name = "4G LTE Only";
    } else {
        $ws46_val = 25; # Auto 5G Preferred
        $preset_name = "Auto 5G Preferred";
    }
    
    my $res = run_at("AT+WS46=$ws46_val");
    if ($res =~ /OK/) {
        print "{\"status\":\"ok\",\"mode\":\"$mode\",\"preset\":\"$preset_name\",\"message\":\"Network mode updated to $preset_name\"}\n";
    } else {
        print "{\"status\":\"error\",\"message\":\"Failed to set network mode\"}\n";
    }
} else {
    my $res = run_at("AT+WS46?");
    my $cur_mode = "auto";
    if ($res =~ /\+WS46:\s*(\d+)/ || $res =~ /(\d+)\s+OK/) {
        my $val = $1;
        if ($val == 30) { $cur_mode = "5g_only"; }
        elsif ($val == 28) { $cur_mode = "4g_only"; }
        else { $cur_mode = "auto"; }
    }
    print "{\"status\":\"ok\",\"mode\":\"$cur_mode\"}\n";
}
