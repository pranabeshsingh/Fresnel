#!/bin/sh
# SimpleAdmin Ultra-Fast Multi-Threaded DNS Cache Warmup Script

DOMAINS="
google.com www.google.com google.co.in youtube.com www.youtube.com youtu.be
googlevideo.com ytimg.com gstatic.com googleapis.com 1e100.net
apple.com www.apple.com icloud.com cdn-apple.com mzstatic.com
cloudflare.com 1.1.1.1 one.one.one.one cdnjs.cloudflare.com
microsoft.com azure.com live.com office.com windows.net bing.com msftconnecttest.com
amazon.com amazon.in aws.amazon.com ssl-images-amazon.com media-amazon.com
github.com githubusercontent.com raw.githubusercontent.com github.io
reddit.com redd.it redditstatic.com redditmedia.com
netflix.com nflxvideo.net nflximg.net nflxext.com
hotstar.com jiocinema.com jio.com jiopay.com jiosaavn.com
spotify.com scdn.co spotifycdn.com
whatsapp.com whatsapp.net web.whatsapp.com
telegram.org t.me api.telegram.org
instagram.com cdninstagram.com facebook.com fbcdn.net meta.com
twitter.com x.com twimg.com
linkedin.com licdn.com
wikipedia.org wikimedia.org
openai.com chatgpt.com oaistatic.com oaiusercontent.com
anthropic.com claude.ai
gemini.google.com
speedtest.net fast.com
flipkart.com fkcdn.com myntra.com swiggy.com zomato.com
paytm.com phonepe.com razorpay.com npci.org.in sbi.co.in hdfcbank.com icicibank.com
cloudflare-dns.com dns.google
akamai.net akamaized.net akamaitechnologies.com
fastly.net map.fastly.net
"

echo "⚡ Starting DNS Cache Warmup for $(echo $DOMAINS | wc -w) top domains..."
t0=$(date +%s)

# Query in parallel (10 background processes at a time)
pids=""
count=0
for d in $DOMAINS; do
    nslookup "$d" 192.168.225.1 >/dev/null 2>&1 &
    pids="$pids $!"
    count=$((count + 1))
    if [ $((count % 15)) -eq 0 ]; then
        wait $pids 2>/dev/null
        pids=""
    fi
done
wait $pids 2>/dev/null

t1=$(date +%s)
diff=$((t1 - t0))
echo "✅ DNS Cache Warmup Complete: $count domains pre-cached in memory in ${diff}s!"
