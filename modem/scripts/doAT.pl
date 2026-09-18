#!/usr/bin/perl
use strict;
use warnings;
use Fcntl qw(:DEFAULT :flock);

my $cmd = $ARGV[0] || "ATI";
$cmd =~ s/\r|\n//g;
my $dev = "/dev/smd7";
my $lock_file = "/tmp/smd7.lock";

open(my $lf, '>>', $lock_file) or die "Lock error: $!\n";
flock($lf, LOCK_EX);

my ($reader, $writer);
pipe($reader, $writer);
my $pid = fork();
if (!defined $pid) {
    flock($lf, LOCK_UN);
    close($lf);
    die "Fork error: $!\n";
}

if ($pid == 0) {
    close $reader;
    sysopen(my $r, $dev, O_RDONLY) or exit(1);
    my $buf;
    my $res = "";
    eval {
        local $SIG{ALRM} = sub { die "timeout\n" };
        alarm(4);
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

print $out;
