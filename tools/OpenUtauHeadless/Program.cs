using System.Collections.Concurrent;
using OpenUtau.Classic;
using OpenUtau.Core;
using OpenUtau.Core.DiffSinger;
using OpenUtau.Core.Format;
using OpenUtau.Core.Render;
using OpenUtau.Core.Ustx;
using OpenUtau.Core.Util;

sealed class PumpSynchronizationContext : SynchronizationContext {
    readonly BlockingCollection<(SendOrPostCallback callback, object? state)> queue = new();

    public override void Post(SendOrPostCallback d, object? state) {
        queue.Add((d, state));
    }

    public void Run() {
        foreach (var item in queue.GetConsumingEnumerable()) {
            item.callback(item.state);
        }
    }

    public void Complete() => queue.CompleteAdding();
}

static class Program {
    static void ConfigureOpenUtau(string singersRoot, int steps) {
        Directory.CreateDirectory(singersRoot);

        Preferences.Default.AdditionalSingerPath = singersRoot;
        Preferences.Default.LoadDeepFolderSinger = true;
        Preferences.Default.DiffSingerSteps = steps;
        Preferences.Default.DiffSingerStepsVariance = Math.Min(steps, 30);
        Preferences.Default.DiffSingerStepsPitch = Math.Min(steps, 20);

        var gpuList = Onnx.getGpuInfo();
        int nvidiaIndex = gpuList.FindIndex(
            g => (g.description ?? "").Contains(
                "NVIDIA", StringComparison.OrdinalIgnoreCase));

        if (OperatingSystem.IsWindows()
            && Onnx.getRunnerOptions().Contains("DirectML")) {
            Preferences.Default.OnnxRunner = "DirectML";
            Preferences.Default.OnnxGpu = nvidiaIndex >= 0 ? nvidiaIndex : 0;
        } else {
            Preferences.Default.OnnxRunner = "CPU";
            Preferences.Default.OnnxGpu = 0;
        }

        Console.WriteLine(
            $"[OpenUtau] ONNX={Preferences.Default.OnnxRunner} "
            + $"GPU={Preferences.Default.OnnxGpu}");
        for (int i = 0; i < gpuList.Count; i++) {
            Console.WriteLine(
                $"[OpenUtau] GPU[{i}] {gpuList[i].description}");
        }

        ToolsManager.Inst.Initialize();
        Console.WriteLine("[OpenUtau] Singer search paths:");
        foreach (var path in PathManager.Inst.SingersPaths) {
            Console.WriteLine($"[OpenUtau]   {path}");
        }

        SingerManager.Inst.Initialize();
    }

    static USinger? FindSinger(string singerHint, string singersRoot) {
        string root = Path.GetFullPath(singersRoot)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);

        var all = SingerManager.Inst.Singers.Values
            .Where(s => s.SingerType == USingerType.DiffSinger)
            .ToList();

        Console.WriteLine($"[OpenUtau] DiffSinger candidates: {all.Count}");
        foreach (var s in all.OrderBy(s => s.Name)) {
            Console.WriteLine(
                $"[OpenUtau] Singer candidate: name='{s.Name}' "
                + $"id='{s.Id}' location='{s.Location}'");
        }

        var local = all.Where(s => {
            try {
                string location = Path.GetFullPath(s.Location ?? string.Empty)
                    .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                return location.StartsWith(
                    root + Path.DirectorySeparatorChar,
                    StringComparison.OrdinalIgnoreCase)
                    || string.Equals(location, root, StringComparison.OrdinalIgnoreCase);
            } catch {
                return false;
            }
        }).ToList();

        var pool = local.Count > 0 ? local : all;

        var hinted = pool
            .Where(s =>
                (s.Name ?? string.Empty).Contains(
                    singerHint, StringComparison.OrdinalIgnoreCase)
                || (s.Id ?? string.Empty).Contains(
                    singerHint, StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(s => {
                try {
                    string location = Path.GetFullPath(s.Location ?? string.Empty);
                    return location.Contains(
                        Path.Combine("Nero_v170", "configs"),
                        StringComparison.OrdinalIgnoreCase);
                } catch {
                    return false;
                }
            })
            .ThenByDescending(s => (s.Id ?? string.Empty).Length)
            .FirstOrDefault();

        if (hinted != null) {
            return hinted;
        }

        if (pool.Count == 1) {
            Console.WriteLine(
                $"[OpenUtau] Singer name does not contain '{singerHint}', "
                + $"using the only DiffSinger candidate: {pool[0].Name}");
            return pool[0];
        }

        return pool
            .OrderBy(s => s.Name)
            .FirstOrDefault();
    }

    static async Task<int> ProbeAsync(string[] args) {
        if (args.Length < 3) {
            Console.Error.WriteLine(
                "Usage: OpenUtauHeadless --probe <singers-root> <singer-hint>");
            return 2;
        }

        string singersRoot = Path.GetFullPath(args[1]);
        string singerHint = args[2];

        ConfigureOpenUtau(singersRoot, 20);

        var singer = FindSinger(singerHint, singersRoot);
        if (singer == null) {
            Console.Error.WriteLine(
                $"No DiffSinger voicebank matching '{singerHint}' "
                + $"found under: {singersRoot}");
            return 3;
        }

        singer.EnsureLoaded();

        var phonemizer = new DiffSingerUkrainianPhonemizer();
        Console.WriteLine(
            $"[OpenUtau] Singer OK: {singer.Name} | "
            + $"id={singer.Id} | version={singer.Version}");
        Console.WriteLine(
            $"[OpenUtau] Ukrainian phonemizer OK: "
            + $"{phonemizer.GetType().FullName}");
        Console.WriteLine("[OpenUtau] Probe OK.");
        await Task.CompletedTask;
        return 0;
    }

    static async Task<int> RenderAsync(string[] args) {
        if (args.Length > 0
            && string.Equals(
                args[0], "--probe", StringComparison.OrdinalIgnoreCase)) {
            return await ProbeAsync(args);
        }

        if (args.Length < 4) {
            Console.Error.WriteLine(
                "Usage: OpenUtauHeadless <project.ustx> <output.wav> "
                + "<singers-root> <singer-hint> [steps]");
            return 2;
        }

        string ustxPath = Path.GetFullPath(args[0]);
        string outputPath = Path.GetFullPath(args[1]);
        string singersRoot = Path.GetFullPath(args[2]);
        string singerHint = args[3];
        int steps = args.Length >= 5
            && int.TryParse(args[4], out int parsedSteps)
                ? Math.Clamp(parsedSteps, 5, 100)
                : 30;

        Directory.CreateDirectory(
            Path.GetDirectoryName(outputPath)!);

        ConfigureOpenUtau(singersRoot, steps);

        var singer = FindSinger(singerHint, singersRoot);
        if (singer == null) {
            Console.Error.WriteLine(
                $"No DiffSinger voicebank matching '{singerHint}' "
                + $"found under: {singersRoot}");
            return 3;
        }

        Console.WriteLine(
            $"[OpenUtau] Singer: {singer.Name} | "
            + $"id={singer.Id} | version={singer.Version}");

        var project = Ustx.Load(ustxPath);
        if (project.tracks.Count == 0) {
            Console.Error.WriteLine("USTX contains no tracks.");
            return 4;
        }

        // PhonemizerRunner delivers its async result only when the part belongs
        // to DocManager.Inst.Project. Previously we validated a standalone UProject,
        // so every phonemizer response was silently discarded.
        DocManager.Inst.ExecuteCmd(new LoadProjectNotification(project));
        project = DocManager.Inst.Project;

        var track = project.tracks[0];
        track.Singer = singer;
        track.Phonemizer = new DiffSingerUkrainianPhonemizer();
        track.RendererSettings.renderer = Renderers.DIFFSINGER;

        singer.EnsureLoaded();

        var voiceParts = project.parts
            .OfType<UVoicePart>()
            .ToList();
        if (voiceParts.Count == 0) {
            Console.Error.WriteLine("USTX contains no voice parts.");
            return 5;
        }

        Console.WriteLine(
            $"[OpenUtau] Project loaded into DocManager: "
            + $"{voiceParts.Count} voice part(s), "
            + $"{voiceParts.Sum(p => p.notes.Count)} note(s).");

        // Trigger phonemization exactly once. Re-validating with
        // SkipPhonemizer=false in a polling loop changes notesTimestamp on every
        // pass, making valid async responses stale before they can land.
        project.ValidateFull();

        var deadline = DateTime.UtcNow.AddMinutes(5);
        var nextStatus = DateTime.UtcNow;
        while (DateTime.UtcNow < deadline) {
            bool phonemesReady = voiceParts.All(p => p.PhonemesUpToDate);
            bool phrasesReady = voiceParts.All(p => p.renderPhrases.Count > 0);

            if (phonemesReady && phrasesReady) {
                break;
            }

            if (DateTime.UtcNow >= nextStatus) {
                Console.WriteLine(
                    $"[OpenUtau] Waiting: phonemes="
                    + $"{voiceParts.Count(p => p.PhonemesUpToDate)}/{voiceParts.Count}, "
                    + $"phrases="
                    + $"{voiceParts.Count(p => p.renderPhrases.Count > 0)}/{voiceParts.Count}");
                nextStatus = DateTime.UtcNow.AddSeconds(5);
            }

            await Task.Delay(100);
        }

        if (!voiceParts.All(p => p.PhonemesUpToDate)) {
            Console.Error.WriteLine(
                "Timed out waiting for Ukrainian phonemization.");
            return 6;
        }
        if (!voiceParts.All(p => p.renderPhrases.Count > 0)) {
            Console.Error.WriteLine(
                "Phonemization completed, but render phrases were not built.");
            return 8;
        }

        Console.WriteLine(
            $"[OpenUtau] Phonemization ready: "
            + $"{voiceParts.Sum(p => p.phonemes.Count)} phoneme(s), "
            + $"{voiceParts.Sum(p => p.renderPhrases.Count)} phrase(s).");

        Console.WriteLine(
            $"[OpenUtau] Rendering DiffSinger, steps={steps}...");
        if (File.Exists(outputPath)) {
            File.Delete(outputPath);
        }

        await PlaybackManager.Inst.RenderMixdown(
            project, outputPath);

        if (!File.Exists(outputPath)
            || new FileInfo(outputPath).Length < 4096) {
            Console.Error.WriteLine(
                "DiffSinger render did not produce a valid WAV.");
            return 7;
        }

        Console.WriteLine(
            $"[OpenUtau] Rendered: {outputPath}");
        return 0;
    }

    public static int Main(string[] args) {
        // OpenUtau's real GUI entry point registers legacy code pages before
        // loading any singer metadata. VoicebankLoader defaults character.txt
        // to Shift-JIS; without this provider .NET throws internally and the
        // singer is silently dropped from SearchAll().
        System.Text.Encoding.RegisterProvider(
            System.Text.CodePagesEncodingProvider.Instance);

        var context = new PumpSynchronizationContext();
        SynchronizationContext.SetSynchronizationContext(context);
        var scheduler =
            TaskScheduler.FromCurrentSynchronizationContext();

        DocManager.Inst.Initialize(
            Thread.CurrentThread, scheduler);
        DocManager.Inst.PostOnUIThread = action =>
            context.Post(_ => action(), null);

        var task = RenderAsync(args);
        _ = task.ContinueWith(
            _ => context.Complete(),
            CancellationToken.None,
            TaskContinuationOptions.None,
            TaskScheduler.Default);

        context.Run();
        return task.GetAwaiter().GetResult();
    }
}
