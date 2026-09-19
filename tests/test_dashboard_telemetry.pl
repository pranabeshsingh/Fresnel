#!/usr/bin/env perl
use strict;
use warnings;
use File::Temp qw(tempdir);

# Check syntax of get_dashboard_data.pl
my $syntax_check = `perl -c modem/scripts/get_dashboard_data.pl 2>&1`;
if ($syntax_check !~ /syntax OK/) {
    die "FAIL: get_dashboard_data.pl syntax error: $syntax_check\n";
}

# Check that services.telemetry exists in the script text
open(my $fh, "<", "modem/scripts/get_dashboard_data.pl") or die "Cannot open script: $!";
my $content = do { local $/; <$fh> };
close($fh);

if ($content !~ /"telemetry":\s*\{/s) {
    die "FAIL: get_dashboard_data.pl does not contain \"telemetry\" service definition in JSON\n";
}

if ($content !~ /telemetry_enabled\s*=>\s*0/) {
    die "FAIL: get_dashboard_data.pl does not default telemetry_enabled to 0\n";
}

print "ALL DASHBOARD TELEMETRY TESTS PASSED\n";
