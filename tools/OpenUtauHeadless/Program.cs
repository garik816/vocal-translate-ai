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
    static async Task<int> RenderAsync(string[] args) {
        if (args.Length < 4) {
            Console.Error.WriteLine(
                "Usage: OpenUtauHeadless <project.ustx> <output.wav> <singers-root> <singer-hint> [steps]");
            return 2;
        }

        string ustxPath = Path.GetFullPath(args[0]);
        string outputPath = Path.GetFullPath(args[1]);
        string singersRoot = Path.GetFullPath(args[2]);
        string singerHint = args[3];
        int steps = args.Length >= 5 && int.TryParse(args[4], out int parsedSteps)
            ? Math.Clamp(parsedSteps, 5, 100)
            : 30;

        Directory.CreateDirectory(singersRoot);
        Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);

        Preferences.Default.AdditionalSingerPath = singersRoot;
        Preferences.Default.LoadDeepFolderSinger = true;
        Preferences.Default.DiffSingerSteps = steps;
        Preferences.Default.DiffSingerStepsVariance = Math.Min(steps, 30);
        Preferences.Default.DiffSingerStepsPitch = Math.Min(steps, 20);

        var gpuList = Onnx.getGpuInfo();
        int nvidiaIndex = gpuList.FindIndex(
            g => (g.description ?? "").Contains("NVIDIA", StringComparison.OrdinalIgnoreCase));

        if (OperatingSystem.IsWindows() && Onnx.getRunnerOptions().Contains("DirectML")) {
            Preferences.Default.OnnxRunner = "DirectML";
            Preferences.Default.OnnxGpu = nvidiaIndex >= 0 ? nvidiaIndex : 0;
        } else {
            Preferences.Default.OnnxRunner = "CPU";
            Preferences.Default.OnnxGpu = 0;
        }

        Console.WriteLine(
            $"[OpenUtau] ONNX={Preferences.Default.OnnxRunner} GPU={Preferences.Default.OnnxGpu}");
        if (gpuList.Count > 0) {
            for (int i = 0; i < gpuList.Count; i++) {
                Console.WriteLine($"[OpenUtau] GPU[{i}] {gpuList[i].description}");
            }
        }

        ToolsManager.Inst.Initialize();
        SingerManager.Inst.Initialize();

        var singer = SingerManager.Inst.Singers.Values
            .Where(s => s.SingerType == USingerType.DiffSinger)
            .OrderByDescending(s =>
                s.Name.Contains(singerHint, StringComparison.OrdinalIgnoreCase)
                || s.Id.Contains(singerHint, StringComparison.OrdinalIgnoreCase))
            .ThenBy(s => s.Name)
            .FirstOrDefault();

        if (singer == null) {
            Console.Error.WriteLine(
                $"No DiffSinger voicebank found under: {singersRoot}");
            return 3;
        }

        Console.WriteLine(
            $"[OpenUtau] Singer: {singer.Name} | id={singer.Id} | version={singer.Version}");

        var project = Ustx.Load(ustxPath);
        if (project.tracks.Count == 0) {
            Console.Error.WriteLine("USTX contains no tracks.");
            return 4;
        }

        var track = project.tracks[0];
        track.Singer = singer;
        track.Phonemizer = new DiffSingerUkrainianPhonemizer();
        track.RendererSettings.renderer = Renderers.DIFFSINGER;

        singer.EnsureLoaded();
        project.ValidateFull();

        var voiceParts = project.parts.OfType<UVoicePart>().ToList();
        if (voiceParts.Count == 0) {
            Console.Error.WriteLine("USTX contains no voice parts.");
            return 5;
        }

        var deadline = DateTime.UtcNow.AddSeconds(90);
        while (DateTime.UtcNow < deadline) {
            project.Validate(new ValidateOptions {
                SkipTiming = true,
                SkipPhonemizer = false,
                SkipPhoneme = false,
            });

            bool ready = voiceParts.All(
                p => p.PhonemesUpToDate && p.renderPhrases.Count > 0);
            if (ready) {
                break;
            }
            await Task.Delay(100);
        }

        if (!voiceParts.All(p => p.PhonemesUpToDate)) {
            Console.Error.WriteLine("Timed out waiting for Ukrainian phonemization.");
            return 6;
        }

        Console.WriteLine($"[OpenUtau] Rendering DiffSinger, steps={steps}...");
        if (File.Exists(outputPath)) {
            File.Delete(outputPath);
        }

        await PlaybackManager.Inst.RenderMixdown(project, outputPath);

        if (!File.Exists(outputPath) || new FileInfo(outputPath).Length < 4096) {
            Console.Error.WriteLine("DiffSinger render did not produce a valid WAV.");
            return 7;
        }

        Console.WriteLine($"[OpenUtau] Rendered: {outputPath}");
        return 0;
    }

    public static int Main(string[] args) {
        var context = new PumpSynchronizationContext();
        SynchronizationContext.SetSynchronizationContext(context);
        var scheduler = TaskScheduler.FromCurrentSynchronizationContext();

        DocManager.Inst.Initialize(Thread.CurrentThread, scheduler);
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
