#!/bin/sh
# Real-time Power & Thermal Monitor for Qualcomm SDX55 (Fresnel 5G Suite)

clear
while true; do
    perl -e '
    use strict;
    use warnings;

    my $vph = 0;
    if (open(my $fh, "<", "/sys/bus/iio/devices/iio:device0/in_voltage_vph_pwr_input")) {
        $vph = <$fh>; chomp($vph); close($fh);
    }
    my $vref = 0;
    if (open(my $fh, "<", "/sys/bus/iio/devices/iio:device0/in_voltage_vref_1p25_input")) {
        $vref = <$fh>; chomp($vref); close($fh);
    }

    my %tz;
    opendir(my $dh, "/sys/class/thermal") or die $!;
    while (my $entry = readdir($dh)) {
        next unless $entry =~ /^thermal_zone\d+$/;
        my $dir = "/sys/class/thermal/$entry";
        if (open(my $tfh, "<", "$dir/type")) {
            my $type = <$tfh>; chomp($type); close($tfh);
            if (open(my $tempfh, "<", "$dir/temp")) {
                my $t = <$tempfh>; chomp($t) if $t; close($tempfh);
                $tz{$type} = $t if defined $t && $t > 0;
            }
        }
    }
    closedir($dh);

    my $vph_v = $vph / 1000000;
    my $status = "HEALTHY (Nominal)";
    if ($vph_v < 3.20) {
        $status = "CRITICAL (USB 2.0 Voltage Sag!)";
    } elsif ($vph_v < 3.30) {
        $status = "MARGINAL (High Load)";
    }

    print "\033[H\033[J";
    print "========================================================\n";
    print "     Qualcomm SDX55 Real-Time Power & Thermal Monitor    \n";
    print "========================================================\n";
    printf("  Main Power Rail (Vph):      %.3f V   [%s]\n", $vph_v, $status);
    printf("  Internal Reference (Vref):  %.4f V\n", $vref / 1000000);
    print "--------------------------------------------------------\n";
    print "  RF Power Amplifiers:\n";
    printf("    PA1 Sub-6GHz:             %.1f C\n", ($tz{"pa-therm1-usr"} || $tz{"modem-lte-sub6-pa1"} || 0) / 1000);
    printf("    PA2 Sub-6GHz:             %.1f C\n", ($tz{"pa-therm2-usr"} || $tz{"modem-lte-sub6-pa2"} || 0) / 1000);
    printf("    TCXO Master RF Clock:     %.1f C\n", ($tz{"xo-therm-usr"} || 0) / 1000);
    print "--------------------------------------------------------\n";
    print "  Silicon Processing Cores:\n";
    printf("    ARM Cortex-A7 CPU:        %.1f C\n", ($tz{"cpu0-a7-usr"} || 0) / 1000);
    printf("    Hexagon QDSP6 Modem Core: %.1f C\n", ($tz{"mdm-q6-usr"} || 0) / 1000);
    printf("    5G Baseband Accelerator:  %.1f C\n", ($tz{"mdm-5g-usr"} || 0) / 1000);
    printf("    Qualcomm IPA Packet Engine:%.1f C\n", ($tz{"ipa-usr"} || 0) / 1000);
    printf("    PMIC Power Controller:    %.1f C\n", ($tz{"pmxprairie_tz"} || 0) / 1000);
    print "--------------------------------------------------------\n";
    print "  Chassis & Environmental:\n";
    printf("    Outer Enclosure Case:     %.1f C\n", ($tz{"sdx-case-therm-usr"} || 0) / 1000);
    printf("    Internal Air Ambient:     %.1f C\n", ($tz{"ambient-therm-usr"} || 0) / 1000);
    print "========================================================\n";
    print " Press [Ctrl+C] to exit. Refreshing every 1s...\n";
    '
    sleep 1
done
