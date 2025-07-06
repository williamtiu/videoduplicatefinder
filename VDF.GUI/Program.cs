// /*
//     Copyright (C) 2021 0x90d
//     This file is part of VideoDuplicateFinder
//     VideoDuplicateFinder is free software: you can redistribute it and/or modify
//     it under the terms of the GPLv3 as published by
//     the Free Software Foundation, either version 3 of the License, or
//     (at your option) any later version.
//     VideoDuplicateFinder is distributed in the hope that it will be useful,
//     but WITHOUT ANY WARRANTY without even the implied warranty of
//     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//     GNU General Public License for more details.
//     You should have received a copy of the GNU General Public License
//     along with VideoDuplicateFinder.  If not, see <http://www.gnu.org/licenses/>.
// */
//

global using System;
global using System.Collections.Generic;
global using System.IO;
global using System.Threading.Tasks;
using System.CommandLine;
using System.CommandLine.Invocation;
using Avalonia;
using Avalonia.ReactiveUI;

namespace VDF.GUI {
	class Program {
		// Initialization code. Don't use any Avalonia, third-party APIs or any
		// SynchronizationContext-reliant code before AppMain is called: things aren't initialized
		// yet and stuff might break.
		[STAThread]
		public static async Task<int> Main(string[] args) {
			var inputOption = new Option<FileInfo>(
				name: "--input",
				description: "Path to the input video file.") { IsRequired = true };
			inputOption.AddAlias("-i");

			var thresholdOption = new Option<float>(
				name: "--similarity-threshold",
				description: "Similarity threshold (0-1) for frame comparison.",
				getDefaultValue: () => 0.95f);
			thresholdOption.AddAlias("-t");

			var detectCommand = new Command("detect", "Detect duplicate video segments.") {
				inputOption,
				thresholdOption
			};

			detectCommand.SetHandler(async (FileInfo inputFile, float threshold) => {
				await HandleDetectCommand(inputFile, threshold);
			}, inputOption, thresholdOption);

			var rootCommand = new RootCommand("Video Duplicate Finder CLI / GUI") {
				detectCommand
			};

			// If args are present and a command is specified, System.CommandLine will handle it.
			// Parse the arguments to determine if a CLI command was invoked.
			var parseResult = rootCommand.Parse(args);

			if (parseResult.CommandResult.Command == detectCommand && parseResult.Errors.Count == 0) {
				// 'detect' command was successfully parsed, invoke it.
				return await rootCommand.InvokeAsync(args);
			}
			else if (args.Length > 0 && (args[0] == "--help" || args[0] == "-h" || args[0] == "/?" || parseResult.Errors.Count > 0)) {
				// If help is requested or there are parsing errors for CLI, let System.CommandLine handle it and exit.
				// This also catches cases where 'detect' might be misspelled or options are wrong.
				return await rootCommand.InvokeAsync(args);
			}
			else {
				// No relevant CLI args, or no args at all, launch GUI
				BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
				return 0; // GUI launched, return success
			}
		}

		private static Task HandleDetectCommand(FileInfo inputFile, float threshold) {
			Console.WriteLine($"Detect command called:");
			Console.WriteLine($"  Input: {inputFile.FullName}");
			Console.WriteLine($"  Similarity Threshold: {threshold}");
			// Here, you would call the actual duplicate detection logic.
			// For now, we just print the arguments.
			// Example: var duplicates = VideoDuplicateDetector.Detect(inputFile.FullName, threshold);
			// Console.WriteLine($"Found {duplicates.Count} duplicates.");

			try {
				var analysisEngine = new VDF.Core.VideoAnalysisEngine();
				var duplicateSegments = analysisEngine.DetectDuplicateSegments(inputFile.FullName, threshold);

				if (duplicateSegments.Any()) {
					Console.WriteLine($"Found {duplicateSegments.Count} potential duplicate segment(s):");
					foreach (var segment in duplicateSegments) {
						Console.WriteLine($"  - StartFrame: {segment.StartFrame}, EndFrame: {segment.EndFrame}");
					}
				}
				else {
					Console.WriteLine("No duplicate segments detected.");
				}
			}
			catch (Exception ex) {
				Console.ForegroundColor = ConsoleColor.Red;
				Console.WriteLine($"Error during detection: {ex.Message}");
				Console.ResetColor();
				// Optionally, rethrow or handle more gracefully
			}

			return Task.CompletedTask;
		}

		// Avalonia configuration, don't remove; also used by visual designer.
		public static AppBuilder BuildAvaloniaApp()
			=> AppBuilder.Configure<App>()
				.UsePlatformDetect()
				.With(new X11PlatformOptions { UseDBusFilePicker = false }) // TODO: Check if this option is still relevant or needs adjustment
				.UseReactiveUI();
	}
}
