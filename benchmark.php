<?php
/**
 * PressPanel Bandwidth & Rate Limit Benchmark Tool
 * Tự động đo lường tốc độ truyền tải thực tế (KB/s, MB/s) qua Nginx FastCGI và File tĩnh Nginx
 */

// Tắt toàn bộ bộ đệm output để dữ liệu được truyền thẳng tới Nginx theo luồng tức thì
if (function_exists('apache_setenv')) {
    @apache_setenv('no-gzip', 1);
}
@ini_set('zlib.output_compression', 'Off');
@ini_set('output_buffering', 'Off');
@ini_set('implicit_flush', 1);

// Chế độ Download dữ liệu raw PHP FastCGI
if (isset($_GET['download']) || (isset($_GET['action']) && $_GET['action'] === 'download')) {
    while (ob_get_level() > 0) {
        ob_end_clean();
    }
    
    $mb = isset($_GET['mb']) ? floatval($_GET['mb']) : 1.0;
    if ($mb <= 0) $mb = 1.0;
    if ($mb > 50) $mb = 50.0; // Giới hạn an toàn tối đa 50MB
    $total_bytes = (int)($mb * 1024 * 1024);

    header('Content-Type: application/octet-stream');
    header('Content-Disposition: attachment; filename="benchmark_' . $mb . 'MB.bin"');
    header('Content-Length: ' . $total_bytes);
    header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
    header('Pragma: no-cache');
    header('Expires: 0');
    // Lưu ý: Không dùng 'X-Accel-Buffering: no' vì sẽ vô hiệu hóa bộ lọc limit_rate của Nginx
    header('X-Benchmark-Bytes: ' . $total_bytes);

    // Chuẩn bị khối dữ liệu 64 KB
    $chunk_size = 65536; // 64 KB
    $sample = "PRESSPANEL-RATE-LIMIT-BENCHMARK-STREAM-TEST-DATA-BLOCK-0123456789ABCDEF\n";
    $repeat_count = (int)ceil($chunk_size / strlen($sample));
    $chunk = substr(str_repeat($sample, $repeat_count), 0, $chunk_size);
    
    $sent = 0;
    while ($sent < $total_bytes) {
        $to_send = min($chunk_size, $total_bytes - $sent);
        echo substr($chunk, 0, $to_send);
        $sent += $to_send;
        flush();
        if (connection_status() != 0) {
            break;
        }
    }
    exit;
}

// Chế độ API trả về thông tin cấu hình server & tình trạng file static
if (isset($_GET['action']) && $_GET['action'] === 'info') {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode([
        'server_name'     => $_SERVER['SERVER_NAME'] ?? 'presspanel.local',
        'server_software' => $_SERVER['SERVER_SOFTWARE'] ?? 'Nginx',
        'server_protocol' => $_SERVER['SERVER_PROTOCOL'] ?? 'HTTP/2.0',
        'php_version'     => PHP_VERSION,
        'php_sapi'        => PHP_SAPI,
        'has_1mb'         => file_exists(__DIR__ . '/test1mb.bin'),
        'has_2mb'         => file_exists(__DIR__ . '/test2mb.bin'),
        'has_5mb'         => file_exists(__DIR__ . '/test5mb.bin'),
        'time'            => date('Y-m-d H:i:s'),
    ], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);
    exit;
}
?>
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⚡ Bandwidth & Rate Limit Benchmark — PressPanel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Plus Jakarta Sans', sans-serif; }
        .font-mono { font-family: 'JetBrains Mono', monospace; }
        .glass { background: rgba(18, 24, 36, 0.85); backdrop-filter: blur(12px); }
        .tab-active { background: rgba(245, 158, 11, 0.15); border-color: rgba(245, 158, 11, 0.5); color: #fbbf24; }
    </style>
</head>
<body class="bg-[#0b0f17] text-slate-200 min-h-screen flex flex-col items-center justify-center p-4 sm:p-6 selection:bg-amber-500 selection:text-black">

    <div class="max-w-2xl w-full space-y-6">

        <!-- Header Card -->
        <div class="glass rounded-2xl p-6 border border-slate-800 shadow-2xl relative overflow-hidden">
            <div class="absolute -top-12 -right-12 w-40 h-40 bg-amber-500/10 rounded-full blur-3xl pointer-events-none"></div>
            <div class="absolute -bottom-12 -left-12 w-40 h-40 bg-blue-500/10 rounded-full blur-3xl pointer-events-none"></div>

            <div class="flex items-center justify-between border-b border-slate-800 pb-4 mb-4">
                <div class="flex items-center space-x-3">
                    <div class="w-11 h-11 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400 flex items-center justify-center text-xl shadow-inner font-bold">
                        ⚡
                    </div>
                    <div>
                        <h1 class="text-lg font-extrabold text-white tracking-tight flex items-center gap-2">
                            <span>Rate Limit Benchmark Tool</span>
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Live</span>
                        </h1>
                        <p class="text-xs text-slate-400">Kiểm tra tốc độ tải dữ liệu thực tế để đối chiếu với cấu hình Rate Limit của Nginx</p>
                    </div>
                </div>
                <div class="text-right font-mono text-[11px] text-slate-400 hidden sm:block">
                    <div class="text-white font-bold"><?= htmlspecialchars($_SERVER['SERVER_NAME'] ?? 'presspanel.local') ?></div>
                    <div class="text-slate-500"><?= htmlspecialchars($_SERVER['SERVER_PROTOCOL'] ?? 'HTTP/2') ?> • PHP <?= PHP_VERSION ?></div>
                </div>
            </div>

            <!-- Target Rate Limit Selector & Info -->
            <div class="space-y-2">
                <div class="flex items-center justify-between text-xs">
                    <span class="text-slate-400 font-semibold">Chọn Mốc Hạn Mức Để So Sánh Đối Chiếu:</span>
                    <span class="text-slate-500 text-[11px] font-mono" id="selectedRateLabel">Đang test theo mốc 256k</span>
                </div>
                <div class="grid grid-cols-4 gap-2 text-center text-xs">
                    <button type="button" onclick="setTargetRate('256k')" id="rateBtn_256k" class="py-2 px-1 rounded-xl font-mono text-xs font-bold transition border border-amber-500/50 bg-amber-500/15 text-amber-400">
                        256 KB/s
                    </button>
                    <button type="button" onclick="setTargetRate('512k')" id="rateBtn_512k" class="py-2 px-1 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white">
                        512 KB/s
                    </button>
                    <button type="button" onclick="setTargetRate('1m')" id="rateBtn_1m" class="py-2 px-1 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white">
                        1 MB/s
                    </button>
                    <button type="button" onclick="setTargetRate('0')" id="rateBtn_0" class="py-2 px-1 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white">
                        Vô Hạn (0)
                    </button>
                </div>

                <!-- Rate Limit Metrics Grid -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 text-center text-xs">
                    <div class="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800">
                        <div class="text-[10px] text-slate-500 font-medium">Băng Thông Mong Đợi</div>
                        <div class="font-bold text-amber-400 font-mono text-xs mt-0.5" id="targetBwText">256 KB/s</div>
                    </div>
                    <div class="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800">
                        <div class="text-[10px] text-slate-500 font-medium">Tương Đương Mạng</div>
                        <div class="font-bold text-slate-300 font-mono text-xs mt-0.5" id="targetMbpsText">≈ 2.05 Mbps</div>
                    </div>
                    <div class="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800">
                        <div class="text-[10px] text-slate-500 font-medium">Thời Gian Tải 1MB</div>
                        <div class="font-bold text-sky-400 font-mono text-xs mt-0.5" id="target1MbTime">≈ 4.0 giây</div>
                    </div>
                    <div class="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800">
                        <div class="text-[10px] text-slate-500 font-medium">Thời Gian Tải 2MB</div>
                        <div class="font-bold text-indigo-400 font-mono text-xs mt-0.5" id="target2MbTime">≈ 8.0 giây</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Benchmark Execution & Gauge Card -->
        <div class="glass rounded-2xl p-6 border border-slate-800 shadow-2xl space-y-6">

            <!-- Mode Selector Tabs -->
            <div>
                <label class="block text-xs font-semibold text-slate-400 mb-2">Chế Độ Đo Lường:</label>
                <div class="grid grid-cols-2 gap-2">
                    <button type="button" onclick="setMode('php')" id="modeBtn_php" class="py-2.5 px-3 rounded-xl font-mono text-xs font-bold transition border border-amber-500/50 bg-amber-500/10 text-amber-400 flex items-center justify-center gap-1.5">
                        <span>⚡ Luồng Động PHP (FastCGI)</span>
                    </button>
                    <button type="button" onclick="setMode('static')" id="modeBtn_static" class="py-2.5 px-3 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white flex items-center justify-center gap-1.5">
                        <span>📁 File Tĩnh Nginx (Static .bin)</span>
                    </button>
                </div>
            </div>

            <!-- Test Size Buttons -->
            <div>
                <label class="block text-xs font-semibold text-slate-400 mb-2">Chọn Dung Lượng Mẫu Cần Tải:</label>
                <div class="grid grid-cols-3 gap-2">
                    <button type="button" onclick="selectSize(1)" id="btnSize1" class="size-btn py-2.5 rounded-xl font-mono text-xs font-bold transition border border-amber-500/50 bg-amber-500/10 text-amber-400">1 MB</button>
                    <button type="button" onclick="selectSize(2)" id="btnSize2" class="size-btn py-2.5 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white">2 MB</button>
                    <button type="button" onclick="selectSize(5)" id="btnSize5" class="size-btn py-2.5 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white">5 MB</button>
                </div>
            </div>

            <!-- Big Speed Meter Display -->
            <div class="flex flex-col items-center justify-center p-6 rounded-2xl bg-[#0e131d] border border-slate-800/80 relative">
                
                <div class="text-[11px] uppercase tracking-wider text-slate-400 font-bold mb-1">Tốc Độ Tải Thời Gian Thực (Measured Speed)</div>
                
                <div class="flex items-baseline gap-2 my-2">
                    <span id="speedValue" class="text-5xl font-black font-mono text-white tracking-tight">0.0</span>
                    <span id="speedUnit" class="text-xl font-bold font-mono text-amber-400">KB/s</span>
                </div>

                <div id="speedMbps" class="text-xs font-mono text-slate-400 font-medium">0.00 Mbps</div>

                <!-- Progress Bar -->
                <div class="w-full mt-5 space-y-1.5">
                    <div class="flex justify-between text-[11px] font-mono text-slate-400">
                        <span id="progressText">0% đã tải</span>
                        <span id="bytesText">0.0 MB / 1.0 MB</span>
                    </div>
                    <div class="w-full h-3 rounded-full bg-slate-900 border border-slate-800 overflow-hidden p-0.5">
                        <div id="progressBar" class="h-full rounded-full bg-gradient-to-r from-amber-500 to-emerald-400 transition-all duration-100" style="width: 0%"></div>
                    </div>
                    <div class="flex justify-between text-[10px] font-mono text-slate-500 pt-0.5">
                        <span id="timeElapsed">Thời gian: 0.0s</span>
                        <span id="timeRemaining">Còn lại: --</span>
                    </div>
                </div>

            </div>

            <!-- Action Button -->
            <div class="flex gap-3">
                <button type="button" id="startBtn" onclick="runBenchmark()"
                    class="flex-1 py-3.5 rounded-xl bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-400 hover:to-amber-500 text-slate-950 font-extrabold text-sm shadow-lg shadow-amber-500/25 flex items-center justify-center space-x-2 transition active:scale-[0.99]">
                    <span>⚡ Bắt Đầu Kiểm Tra Tốc Độ</span>
                </button>
                <button type="button" id="cancelBtn" onclick="cancelBenchmark()" disabled
                    class="px-5 py-3.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold text-xs border border-slate-700 opacity-40 cursor-not-allowed transition">
                    Dừng
                </button>
            </div>

            <!-- Verdict / Kết Quả Đánh Giá -->
            <div id="verdictBox" class="hidden p-4 rounded-xl border space-y-1.5 transition">
                <div class="flex items-center gap-2 font-bold text-sm" id="verdictTitle">
                    <span id="verdictIcon">✓</span>
                    <span id="verdictHeadline">Kết quả kiểm tra</span>
                </div>
                <p class="text-xs leading-relaxed" id="verdictDesc"></p>
            </div>

        </div>

        <!-- Terminal Command Test Box (Copyable) -->
        <div class="glass rounded-2xl p-5 border border-slate-800 text-xs space-y-2.5">
            <div class="font-bold text-slate-300 flex items-center justify-between">
                <span class="flex items-center gap-1.5">
                    <span>💻</span>
                    <span>Hoặc Kiểm Tra Trực Tiếp Qua Terminal (cURL):</span>
                </span>
                <span class="text-[10px] text-slate-500 font-mono">Linux / PowerShell / WSL</span>
            </div>
            <div class="relative group">
                <pre id="curlCommand" class="p-3 rounded-xl bg-slate-950 border border-slate-800 text-amber-300 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap selection:bg-slate-700">curl -k -o /dev/null -w "Thời gian: %{time_total}s | Tốc độ: %{speed_download} B/s\n" "https://presspanel.local/benchmark.php?download=1&mb=1"</pre>
                <button onclick="copyCurl()" class="absolute top-2 right-2 px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-[10px] font-mono border border-slate-700 transition">Copy</button>
            </div>
            <div class="p-3 rounded-xl bg-slate-900/60 border border-slate-800 text-[11px] text-slate-400 space-y-1">
                <div class="font-semibold text-slate-300">📊 Bảng tham chiếu thời gian tải 1MB theo từng mức Rate Limit:</div>
                <ul class="list-disc list-inside space-y-0.5 text-slate-400 pl-1">
                    <li><b>256k:</b> Tốc độ ~260 KB/s → Tải 1MB mất đúng <span class="text-amber-400 font-mono">~4.0 giây</span> (2MB mất ~8.0s)</li>
                    <li><b>512k:</b> Tốc độ ~510 KB/s → Tải 1MB mất đúng <span class="text-amber-400 font-mono">~2.0 giây</span> (2MB mất ~4.0s)</li>
                    <li><b>1m:</b> Tốc độ ~1000 KB/s → Tải 1MB mất đúng <span class="text-amber-400 font-mono">~1.0 giây</span> (2MB mất ~2.0s)</li>
                    <li><b>0 (Vô hạn):</b> Tải 1MB chỉ mất <span class="text-emerald-400 font-mono">&lt; 0.05 giây</span> (Tốc độ tối đa mạng/CPU)</li>
                </ul>
            </div>
        </div>

    </div>

    <script>
        let selectedMb = 1;
        let selectedMode = 'php'; // 'php' or 'static'
        let targetRate = '256k';
        let abortController = null;
        let isRunning = false;

        const rateProfiles = {
            '256k': { bw: '256 KB/s', mbps: '≈ 2.05 Mbps', t1: '≈ 4.0 giây', t2: '≈ 8.0 giây', minKb: 200, maxKb: 320 },
            '512k': { bw: '512 KB/s', mbps: '≈ 4.10 Mbps', t1: '≈ 2.0 giây', t2: '≈ 4.0 giây', minKb: 450, maxKb: 600 },
            '1m':   { bw: '1.0 MB/s', mbps: '≈ 8.00 Mbps', t1: '≈ 1.0 giây', t2: '≈ 2.0 giây', minKb: 900, maxKb: 1200 },
            '0':    { bw: 'Không Giới Hạn', mbps: 'Tối Đa Cổng Mạng', t1: '< 0.05 giây', t2: '< 0.1 giây', minKb: 5000, maxKb: 9999999 }
        };

        function setTargetRate(rate) {
            targetRate = rate;
            ['256k', '512k', '1m', '0'].forEach(r => {
                const btn = document.getElementById('rateBtn_' + r);
                if (r === rate) {
                    btn.className = 'py-2 px-1 rounded-xl font-mono text-xs font-bold transition border border-amber-500/50 bg-amber-500/15 text-amber-400';
                } else {
                    btn.className = 'py-2 px-1 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white';
                }
            });
            const p = rateProfiles[rate];
            document.getElementById('targetBwText').textContent = p.bw;
            document.getElementById('targetMbpsText').textContent = p.mbps;
            document.getElementById('target1MbTime').textContent = p.t1;
            document.getElementById('target2MbTime').textContent = p.t2;
            document.getElementById('selectedRateLabel').textContent = `Đang đối chiếu theo mốc ${rate.toUpperCase()}`;
        }

        function setMode(mode) {
            if (isRunning) return;
            selectedMode = mode;
            const phpBtn = document.getElementById('modeBtn_php');
            const staticBtn = document.getElementById('modeBtn_static');
            if (mode === 'php') {
                phpBtn.className = 'py-2.5 px-3 rounded-xl font-mono text-xs font-bold transition border border-amber-500/50 bg-amber-500/10 text-amber-400 flex items-center justify-center gap-1.5';
                staticBtn.className = 'py-2.5 px-3 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white flex items-center justify-center gap-1.5';
            } else {
                staticBtn.className = 'py-2.5 px-3 rounded-xl font-mono text-xs font-bold transition border border-amber-500/50 bg-amber-500/10 text-amber-400 flex items-center justify-center gap-1.5';
                phpBtn.className = 'py-2.5 px-3 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white flex items-center justify-center gap-1.5';
            }
            updateCurlCommand();
        }

        function selectSize(mb) {
            if (isRunning) return;
            selectedMb = mb;
            [1, 2, 5].forEach(s => {
                const btn = document.getElementById('btnSize' + s);
                if (s === mb) {
                    btn.className = 'size-btn py-2.5 rounded-xl font-mono text-xs font-bold transition border border-amber-500/50 bg-amber-500/10 text-amber-400';
                } else {
                    btn.className = 'size-btn py-2.5 rounded-xl font-mono text-xs font-bold transition border border-slate-800 bg-slate-900/80 text-slate-400 hover:text-white';
                }
            });
            document.getElementById('bytesText').textContent = `0.0 MB / ${mb}.0 MB`;
            updateCurlCommand();
        }

        function updateCurlCommand() {
            let url = '';
            if (selectedMode === 'php') {
                url = `https://presspanel.local/benchmark.php?download=1&mb=${selectedMb}`;
            } else {
                url = `https://presspanel.local/test${selectedMb}mb.bin`;
            }
            document.getElementById('curlCommand').textContent = `curl -k -o /dev/null -w "Thời gian: %{time_total}s | Tốc độ: %{speed_download} B/s\\n" "${url}"`;
        }

        async function runBenchmark() {
            if (isRunning) return;
            isRunning = true;
            abortController = new AbortController();

            const startBtn = document.getElementById('startBtn');
            const cancelBtn = document.getElementById('cancelBtn');
            const verdictBox = document.getElementById('verdictBox');
            verdictBox.classList.add('hidden');

            startBtn.disabled = true;
            startBtn.classList.add('opacity-60', 'cursor-not-allowed');
            startBtn.innerHTML = '<span>⏳ Đang tải và đo lường luồng dữ liệu...</span>';

            cancelBtn.disabled = false;
            cancelBtn.classList.remove('opacity-40', 'cursor-not-allowed');

            const totalExpectedBytes = selectedMb * 1024 * 1024;
            const startTime = performance.now();
            let receivedBytes = 0;
            let lastSpeedSampleTime = startTime;
            let currentKBps = 0;

            let targetUrl = '';
            if (selectedMode === 'php') {
                targetUrl = `benchmark.php?download=1&mb=${selectedMb}&t=${Date.now()}`;
            } else {
                targetUrl = `test${selectedMb}mb.bin?t=${Date.now()}`;
            }

            try {
                const response = await fetch(targetUrl, {
                    signal: abortController.signal,
                    cache: 'no-store'
                });

                if (!response.ok) {
                    throw new Error(`HTTP Error: ${response.status} ${response.statusText}`);
                }

                const reader = response.body.getReader();

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    receivedBytes += value.length;
                    const now = performance.now();

                    if (now - lastSpeedSampleTime > 100 || receivedBytes === totalExpectedBytes) {
                        const elapsedOverallSec = (now - startTime) / 1000;
                        if (elapsedOverallSec > 0) {
                            currentKBps = (receivedBytes / 1024) / elapsedOverallSec;
                        }
                        
                        const progressPct = Math.min(100, (receivedBytes / totalExpectedBytes) * 100);
                        document.getElementById('progressBar').style.width = progressPct.toFixed(1) + '%';
                        document.getElementById('progressText').textContent = `${progressPct.toFixed(1)}% đã tải`;
                        document.getElementById('bytesText').textContent = `${(receivedBytes / 1048576).toFixed(2)} MB / ${selectedMb}.0 MB`;
                        document.getElementById('timeElapsed').textContent = `Thời gian: ${elapsedOverallSec.toFixed(1)}s`;

                        if (currentKBps > 0) {
                            const remainingBytes = Math.max(0, totalExpectedBytes - receivedBytes);
                            const remainingSec = (remainingBytes / 1024) / currentKBps;
                            document.getElementById('timeRemaining').textContent = `Còn lại: ~${remainingSec.toFixed(1)}s`;
                        }

                        if (currentKBps >= 1024) {
                            document.getElementById('speedValue').textContent = (currentKBps / 1024).toFixed(2);
                            document.getElementById('speedUnit').textContent = 'MB/s';
                        } else {
                            document.getElementById('speedValue').textContent = currentKBps.toFixed(1);
                            document.getElementById('speedUnit').textContent = 'KB/s';
                        }
                        document.getElementById('speedMbps').textContent = `${(currentKBps * 8 / 1024).toFixed(2)} Mbps`;

                        lastSpeedSampleTime = now;
                    }
                }

                const totalDurationSec = (performance.now() - startTime) / 1000;
                const finalAvgKBps = (receivedBytes / 1024) / totalDurationSec;

                showVerdict(finalAvgKBps, totalDurationSec, selectedMb);

            } catch (err) {
                if (err.name === 'AbortError') {
                    showWarning('Đã hủy kiểm tra benchmark.');
                } else {
                    showError(`Lỗi kiểm tra: ${err.message}`);
                }
            } finally {
                isRunning = false;
                startBtn.disabled = false;
                startBtn.classList.remove('opacity-60', 'cursor-not-allowed');
                startBtn.innerHTML = '<span>⚡ Bắt Đầu Kiểm Tra Tốc Độ</span>';

                cancelBtn.disabled = true;
                cancelBtn.classList.add('opacity-40', 'cursor-not-allowed');
            }
        }

        function cancelBenchmark() {
            if (abortController) {
                abortController.abort();
            }
        }

        function showVerdict(speedKBps, durationSec, mb) {
            const box = document.getElementById('verdictBox');
            const headline = document.getElementById('verdictHeadline');
            const desc = document.getElementById('verdictDesc');
            const icon = document.getElementById('verdictIcon');

            box.className = 'p-4 rounded-xl border space-y-1.5 transition block';
            const prof = rateProfiles[targetRate];

            if (targetRate === '0') {
                box.classList.add('bg-emerald-500/10', 'border-emerald-500/30', 'text-emerald-300');
                icon.textContent = '🚀';
                headline.textContent = `Tốc Độ Vô Hạn: ${(speedKBps / 1024).toFixed(1)} MB/s (${speedKBps.toFixed(0)} KB/s)`;
                desc.innerHTML = `Hoàn thành tải ${mb}MB chỉ trong <b>${durationSec.toFixed(3)} giây</b>. Hệ thống đang hoạt động ở chế độ không giới hạn băng thông.`;
            } else if (speedKBps >= prof.minKb && speedKBps <= prof.maxKb) {
                box.classList.add('bg-emerald-500/10', 'border-emerald-500/30', 'text-emerald-300');
                icon.textContent = '🎉';
                headline.textContent = `Chính Xác 100%: ${speedKBps.toFixed(1)} KB/s (~${(speedKBps * 8 / 1024).toFixed(2)} Mbps)`;
                desc.innerHTML = `Hoàn hảo! Tốc độ đo được khớp hoàn toàn với hạn mức <b>Rate Limit ${targetRate.toUpperCase()}</b> của Nginx.<br>Tải file ${mb}MB hoàn thành trong <b>${durationSec.toFixed(2)} giây</b>.`;
            } else {
                box.classList.add('bg-amber-500/10', 'border-amber-500/30', 'text-amber-300');
                icon.textContent = '⚡';
                headline.textContent = `Tốc Độ Đo Được: ${speedKBps.toFixed(1)} KB/s (${durationSec.toFixed(2)}s)`;
                desc.innerHTML = `Tốc độ thực tế đo được là <b>${speedKBps.toFixed(1)} KB/s</b> (${durationSec.toFixed(2)}s).<br>Hạn mức đang chọn đối chiếu là <b>${targetRate.toUpperCase()}</b> (mong đợi ~${prof.bw}). Hãy kiểm tra xem bạn đã lưu và áp dụng hạn mức này trên PressPanel chưa.`;
            }
        }

        function showWarning(msg) {
            const box = document.getElementById('verdictBox');
            box.className = 'p-4 rounded-xl border bg-amber-500/10 border-amber-500/30 text-amber-300 space-y-1 block';
            document.getElementById('verdictHeadline').textContent = 'Đã Hủy';
            document.getElementById('verdictDesc').textContent = msg;
        }

        function showError(msg) {
            const box = document.getElementById('verdictBox');
            box.className = 'p-4 rounded-xl border bg-rose-500/10 border-rose-500/30 text-rose-300 space-y-1 block';
            document.getElementById('verdictHeadline').textContent = 'Lỗi Đo Lường';
            document.getElementById('verdictDesc').textContent = msg;
        }

        function copyCurl() {
            const text = document.getElementById('curlCommand').innerText;
            navigator.clipboard.writeText(text);
            alert('Đã sao chép lệnh cURL vào clipboard!');
        }
    </script>
</body>
</html>
