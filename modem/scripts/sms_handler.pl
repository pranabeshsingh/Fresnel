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

my $token = $params{'token'} || "";
$token =~ s/[^a-fA-F0-9]//g;

if (length($token) != 32 || ! -f "/tmp/gw_sessions/$token") {
    print '{"status":"error","message":"Unauthorized"}';
    exit(0);
}

my $action = $params{'action'} || "list";
my $dev = "/dev/smd7";
my $lock_file = "/tmp/smd7.lock";

sub at_cmd {
    my ($cmd) = @_;
    open(my $lf, ">>", $lock_file) or return "";
    flock($lf, LOCK_EX);
    my ($r, $w); pipe($r, $w);
    my $pid = fork();
    if (!defined $pid) {
        flock($lf, LOCK_UN);
        close($lf);
        return "";
    }
    if ($pid == 0) {
        close $r;
        sysopen(my $fh, $dev, O_RDONLY) or exit(1);
        my $buf; my $res = "";
        eval {
            local $SIG{ALRM} = sub { die "timeout\n" }; alarm(3);
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

if ($action eq "list") {
    at_cmd("AT+CMGF=1");
    at_cmd("AT+CPMS=\"SM\",\"SM\",\"SM\"");
    my $raw_sm = at_cmd("AT+CMGL=\"ALL\"");
    
    at_cmd("AT+CPMS=\"ME\",\"ME\",\"ME\"");
    my $raw_me = at_cmd("AT+CMGL=\"ALL\"");
    
    my $raw = "$raw_sm\n$raw_me";
    my @messages = ();
    
    # Parse CMGL text lines
    # +CMGL: 1,"REC READ","+919876543210",,"23/10/24,14:20:00+22"
    # Message Body
    my @lines = split(/\r?\n/, $raw);
    for (my $i = 0; $i < @lines; $i++) {
        if ($lines[$i] =~ /\+CMGL:\s*(\d+)\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*(?:,[^,]*,\s*"([^"]+)")?/) {
            my ($idx, $stat, $sender, $date) = ($1, $2, $3, $4 || "Unknown");
            my $body = "";
            if ($i + 1 < @lines && $lines[$i+1] !~ /^\+CMGL:/ && $lines[$i+1] !~ /^OK/ && $lines[$i+1] !~ /^ERROR/) {
                $body = $lines[++$i];
            }
            my $is_otp = ($body =~ /OTP|code|verification|password|balance|recharge|validity/i) ? 1 : 0;
            
            $body =~ s/\\/\\\\/g;
            $body =~ s/"/\\"/g;
            $sender =~ s/"/\\"/g;
            $date =~ s/"/\\"/g;
            
            push @messages, sprintf('{"id":%d,"status":"%s","sender":"%s","date":"%s","body":"%s","is_otp":%d}',
                $idx, $stat, $sender, $date, $body, $is_otp);
        }
    }
    
    my $msg_json = "[" . join(",", @messages) . "]";
    print "{\"status\":\"ok\",\"count\":" . scalar(@messages) . ",\"messages\":$msg_json}";
    exit(0);
}

if ($action eq "send") {
    my $num = $params{'number'} || "";
    my $txt = $params{'text'} || "";
    $num =~ s/[^0-9+]//g;
    $txt =~ s/\x1A//g;  # Strip embedded SUB chars that would prematurely terminate AT+CMGS
    
    if (!$num || !$txt) {
        print '{"status":"error","message":"Invalid recipient or message"}';
        exit(0);
    }
    
    at_cmd("AT+CMGF=1");
    open(my $lf, ">>", $lock_file) or do { print '{"status":"error","message":"Lock error"}'; exit(0); };
    flock($lf, LOCK_EX);
    
    sysopen(my $w, $dev, O_WRONLY);
    syswrite($w, "AT+CMGS=\"$num\"\r");
    select(undef, undef, undef, 0.2);
    syswrite($w, "$txt\x1A");
    close($w);
    
    select(undef, undef, undef, 1.5);
    flock($lf, LOCK_UN);
    close($lf);
    
    print '{"status":"ok","message":"SMS dispatched"}';
    exit(0);
}

if ($action eq "delete") {
    my $id = int($params{'id'} || 0);
    at_cmd("AT+CMGD=$id");
    print '{"status":"ok","message":"Message deleted"}';
    exit(0);
}

print '{"status":"error","message":"Unknown action"}';
