#!/usr/bin/perl
use strict;
use warnings;
use Fcntl qw(:flock O_RDONLY O_WRONLY);

print "Content-type: application/json\r\n";
print "Cache-Control: no-cache\r\n\r\n";

my $query = $ENV{'QUERY_STRING'} || "";
my $post_data = "";
if ($ENV{'REQUEST_METHOD'} && $ENV{'REQUEST_METHOD'} eq 'POST') {
    read(STDIN, $post_data, $ENV{'CONTENT_LENGTH'} || 0);
}
my $params_str = $post_data ? "$query&$post_data" : $query;

my %params = ();
for my $pair (split(/&/, $params_str)) {
    my ($k, $v) = split(/=/, $pair, 2);
    if (defined $k && defined $v) {
        $v =~ s/\+/ /g;
        $v =~ s/%([0-9a-fA-F]{2})/chr(hex($1))/eg;
        $params{$k} = $v;
    }
}

# Validate Auth Token
my $token = $params{'token'} || "";
$token =~ s/[^a-fA-F0-9]//g;

if (length($token) != 32 || ! -f "/tmp/gw_sessions/$token") {
    print '{"status":"error","message":"Unauthorized"}';
    exit(0);
}

my $action = $params{'action'} || "send";
my $code   = $params{'code'} || "";
$code =~ s/[^0-9*#+]//g;

my $dev = "/dev/smd7";
my $lock_file = "/tmp/smd7.lock";

sub decode_hex_ucs2 {
    my ($hex) = @_;
    return $hex if length($hex) % 4 != 0;
    my $str = "";
    for (my $i = 0; $i < length($hex); $i += 4) {
        my $code = hex(substr($hex, $i, 4));
        $str .= ($code < 128) ? chr($code) : " ";
    }
    return $str;
}

sub run_ussd {
    my ($ussd_code) = @_;
    open(my $lf, ">", $lock_file) or return "Lock error";
    flock($lf, LOCK_EX);
    
    my ($reader, $writer);
    pipe($reader, $writer);
    my $pid = fork();
    if (!defined $pid) {
        flock($lf, LOCK_UN);
        close($lf);
        return "Fork error";
    }
    
    if ($pid == 0) {
        close $reader;
        sysopen(my $r, $dev, O_RDONLY) or exit(1);
        my $buf;
        my $res = "";
        eval {
            local $SIG{ALRM} = sub { die "timeout\n" };
            alarm(5);
            while (sysread($r, $buf, 1024)) {
                $res .= $buf;
                last if $res =~ /\+CUSD:/;
                last if $res =~ /(?:ERROR|\+CME ERROR:.*)/;
            }
            alarm(0);
        };
        print $writer $res;
        close $writer;
        exit(0);
    }
    
    close $writer;
    select(undef, undef, undef, 0.05);
    if (sysopen(my $w, $dev, O_WRONLY)) {
        syswrite($w, "AT+CUSD=1,\"$ussd_code\",15\r\n");
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

if ($action eq "send") {
    if (!$code) {
        print '{"status":"error","message":"Please enter a USSD code"}';
        exit(0);
    }

    my $raw = run_ussd($code);
    my $reply = "";
    
    if ($raw =~ /\+CUSD:\s*\d+,"([^"]+)"(?:,(\d+))?/) {
        my ($msg, $dcs) = ($1, $2 || 15);
        if ($msg =~ /^[0-9A-Fa-f]{8,}$/) {
            $reply = decode_hex_ucs2($msg);
        } else {
            $reply = $msg;
        }
    } elsif ($raw =~ /OK/) {
        $reply = "USSD code ($code) sent to network.\n(Note: On 5G NSA data lines without voice, carrier balance alerts are sent via SMS inbox)";
    } else {
        $reply = $raw || "No response received from carrier.";
    }

    # Clean text
    $reply =~ s/\\r/\n/g;
    $reply =~ s/\\n/\n/g;
    $reply =~ s/\\/\\\\/g;
    $reply =~ s/"/\\"/g;
    $reply =~ s/\r//g;
    $reply =~ s/\n/\\n/g;

    print "{\"status\":\"ok\",\"code\":\"$code\",\"response\":\"$reply\"}";
    exit(0);
}

print '{"status":"error","message":"Unknown action"}';
