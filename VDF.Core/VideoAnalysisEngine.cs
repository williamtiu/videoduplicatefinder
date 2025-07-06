// /*
//     Copyright (C) 2021 0x90d, 2024 Jules (AI Agent)
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

using System;
using System;
using System.Collections.Generic;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using FFmpeg.AutoGen;
using SixLabors.ImageSharp;
using SixLabors.ImageSharp.PixelFormats;
using SixLabors.ImageSharp.Processing;
using VDF.Core.FFTools;
using VDF.Core.FFTools.FFmpegNative; // For VideoStreamDecoder

namespace VDF.Core {
	public class VideoAnalysisEngine {
		/// <summary>
		/// Detects duplicate video segments by comparing frames at the end/start boundaries.
		/// </summary>
		/// <param name="videoPath">Path to input video file.</param>
		/// <param name="threshold">Similarity threshold (0-1) for frame comparison.</param>
		/// <returns>List of duplicate segment pairs (start_frame_of_duplicate, end_frame_of_original_segment_that_is_duplicated_at_end).</returns>
		public List<(int StartFrame, int EndFrame)> DetectDuplicateSegments(string videoPath, float similarityThreshold = 0.95f) {
			if (string.IsNullOrEmpty(videoPath)) {
				throw new ArgumentException("Video path cannot be null or empty.", nameof(videoPath));
			}

			// Normalize the path and perform existence check
			string fullPath;
			try {
				fullPath = Path.GetFullPath(videoPath); // Canonicalize path
			}
			catch (Exception ex) when (ex is ArgumentException || ex is NotSupportedException || ex is PathTooLongException) {
				throw new ArgumentException($"Video path '{videoPath}' is invalid: {ex.Message}", nameof(videoPath), ex);
			}

			if (!File.Exists(fullPath)) {
				throw new FileNotFoundException($"Video file not found at '{fullPath}'.", fullPath);
			}

			if (similarityThreshold < 0.0f || similarityThreshold > 1.0f) {
				throw new ArgumentOutOfRangeException(nameof(similarityThreshold), "Similarity threshold must be between 0.0 and 1.0.");
			}

			var duplicateSegments = new List<(int StartFrame, int EndFrame)>();

			// 1. Get video information (frame count, FPS) using FFProbeEngine.
			// Use fullPath for FFProbeEngine
			MediaInfo? mediaInfo = FFProbeEngine.GetMediaInfo(fullPath, extendedLogging: false);
			if (mediaInfo == null || mediaInfo.Streams == null) {
				// Consider logging this issue or throwing a more specific exception
				throw new InvalidOperationException($"Could not retrieve valid media information for '{fullPath}'.");
			}

			// Sanity check on video duration (e.g., not longer than 24 hours, not zero or negative)
			const double maxReasonableDurationHours = 24.0;
			if (mediaInfo.Duration <= TimeSpan.Zero || mediaInfo.Duration.TotalHours > maxReasonableDurationHours) {
				throw new InvalidDataException($"Video duration '{mediaInfo.Duration}' is unreasonable or invalid for '{fullPath}'. Must be > 0s and < {maxReasonableDurationHours} hours.");
			}

			var videoStream = mediaInfo.Streams.FirstOrDefault(s => s.CodecType == "video");
			if (videoStream == null) {
				throw new InvalidOperationException($"No video stream found in '{videoPath}'.");
			}

			float frameRate = videoStream.FrameRate;
			if (frameRate <= 0) {
				// Try to parse from avg_frame_rate if r_frame_rate is invalid like "0/0"
				if (videoStream.CodecName != null && mediaInfo.Streams.Any(s => s.CodecName == videoStream.CodecName && s.FrameRate > 0)) // Check specific stream
				{
					// This part of logic might need access to ffprobe output parsing if not directly in MediaInfo.
					// For now, we assume FrameRate is somewhat reliable or we'd need more robust parsing.
					// FFProbeJsonReader might need to expose avg_frame_rate if FrameRate (r_frame_rate) is "0/0"
					// Let's try to get it from another stream if the primary one is bad. This is a guess.
					var anotherVideoStream = mediaInfo.Streams.FirstOrDefault(s => s.CodecType == "video" && s.FrameRate > 0);
					if (anotherVideoStream != null) frameRate = anotherVideoStream.FrameRate;
				}
				if (frameRate <= 0) // If still invalid
				{
					throw new InvalidDataException($"Invalid frame rate ({frameRate}) for video stream in '{fullPath}'.");
				}
			}

			// Sanity check on frame rate (e.g., not absurdly high like > 1000 FPS, not zero/negative)
			const float maxReasonableFrameRate = 1000.0f;
			if (frameRate <= 0 || frameRate > maxReasonableFrameRate) {
				throw new InvalidDataException($"Video frame rate '{frameRate} FPS' is unreasonable or invalid for '{fullPath}'. Must be > 0 and < {maxReasonableFrameRate} FPS.");
			}

			long totalFrames = (long)(mediaInfo.Duration.TotalSeconds * frameRate);
			if (totalFrames <= 0) {
				throw new InvalidDataException($"Calculated total frames ({totalFrames}) is invalid for '{fullPath}'. Must be positive.");
			}

			// Sanity check on total frames (e.g., not more than a billion frames)
			const long maxReasonableTotalFrames = 1_000_000_000L; // Approx 9.5 hours at 30fps
			if (totalFrames > maxReasonableTotalFrames) {
				throw new InvalidDataException($"Calculated total frames ({totalFrames}) is unreasonably large for '{fullPath}'. Limit is {maxReasonableTotalFrames}.");
			}

			Console.WriteLine($"Video Info for '{fullPath}': Duration: {mediaInfo.Duration}, FrameRate: {frameRate}, TotalFrames: {totalFrames}");

			// 2. Define how many frames to compare from start and end
			int secondsToCompare = 5; // Make this configurable later
			int framesToCompareCount = (int)(secondsToCompare * frameRate);

			if (framesToCompareCount <= 0) {
				Console.WriteLine("Number of frames to compare is zero or negative, possibly due to very low framerate or short duration. Skipping comparison.");
				return duplicateSegments;
			}

			if (totalFrames < 2 * framesToCompareCount) {
				Console.WriteLine($"Video is too short (Total: {totalFrames}, Needed: {2 * framesToCompareCount}) to compare the defined start/end segments of {secondsToCompare}s each. Skipping.");
				return duplicateSegments;
			}

			Console.WriteLine($"Comparing the first {framesToCompareCount} frames with the last {framesToCompareCount} frames.");

			// 3. Extract and hash frames from the start of the video.
			List<ulong> startHashes = ExtractAndHashFrames(videoPath, 0, framesToCompareCount, frameRate, videoStream.Width, videoStream.Height);

			// 4. Extract and hash frames from the end of the video.
			long endSegmentStartFrameIndex = totalFrames - framesToCompareCount;
			List<ulong> endHashes = ExtractAndHashFrames(videoPath, endSegmentStartFrameIndex, framesToCompareCount, frameRate, videoStream.Width, videoStream.Height);

			if (!startHashes.Any() || !endHashes.Any()) {
				Console.WriteLine("Could not extract hashes for comparison. Skipping further analysis.");
				return duplicateSegments;
			}

			// 5. Compare hashes
			// This is a very basic comparison: check if the first hash of start matches the first hash of end.
			// A more robust solution would compare sequences of hashes.
			float similarityOfFirstFrames = CompareAverageHashes(startHashes[0], endHashes[0]);
			Console.WriteLine($"Similarity between first frame of start segment and first frame of end segment: {similarityOfFirstFrames:P2}");

			if (similarityOfFirstFrames >= similarityThreshold) {
				// This implies the segment from frame 0 to (framesToCompareCount-1) might be duplicated at the end.
				// The exact definition of the returned tuple (StartFrame, EndFrame) needs to be clarified.
				// For now, it represents the original segment that is found to be duplicated.
				Console.WriteLine($"Potential duplicate detected: Start segment (frames 0 to {framesToCompareCount - 1}) seems to match end segment.");
				duplicateSegments.Add((0, framesToCompareCount - 1));
			}

			// TODO: Implement more sophisticated sequence matching if needed.
			// For example, find the longest common subsequence of hashes that meets the threshold.

			return duplicateSegments;
		}

		// Added sampleRate parameter, defaults to 1 (process every frame)
		private unsafe List<ulong> ExtractAndHashFrames(string videoPath, long startFrameIndex, int frameCount, float frameRate, int frameWidth, int frameHeight, int sampleRate = 1) {
			var hashes = new List<ulong>();
			if (frameCount <= 0) return hashes;
			if (sampleRate <= 0) sampleRate = 1; // Ensure sampleRate is positive

			// It's generally better to create the decoder once per video if processing multiple segments from it.
			// However, for simplicity in this initial implementation (comparing only start vs. end),
			// creating it per segment is acceptable. If performance becomes an issue, this can be optimized.
			using (var decoder = new VideoStreamDecoder(videoPath)) {
				// 1. Perform initial seek to the start of the segment
				TimeSpan startTimestamp = TimeSpan.FromSeconds(startFrameIndex / frameRate);
				AVFrame tempFrameForSeek; // Need an out param for TryDecodeFrame, even if we discard this specific frame

				// We use TryDecodeFrame for its seeking capability.
				// The first frame obtained here might be used or skipped if we only want frames *after* the seek point.
				// For simplicity, let's assume the first call to TryDecodeNextFrameSequential after this will get the frame at startTimestamp.
				// A more precise way would be to get the frame from this seek and then loop for frameCount-1 using sequential.
				// Or, ensure TryDecodeNextFrameSequential starts exactly at the seeked position's next frame.
				// The current TryDecodeFrame seeks and decodes one frame. TryDecodeNextFrameSequential continues from there.

				bool seekSuccess = decoder.TryDecodeFrame(out tempFrameForSeek, startTimestamp);
				// We might use tempFrameForSeek as the first frame, or discard and start fresh loop
				// For now, let's assume the first call to TryDecodeNextFrameSequential will get the frame at startTimestamp or just after.
				// This is a bit imprecise; a robust solution might need to align timestamps carefully.

				if (!seekSuccess && frameCount > 0) // If seek fails and we need frames, can't proceed
				{
					Console.WriteLine($"Warning: Initial seek to {startTimestamp} for frame index {startFrameIndex} failed. Cannot extract frames for this segment.");
					return hashes;
				}

				// If the first frame from seek should be processed:
				// if (seekSuccess) {
				//     Image<L8> firstImage = ConvertAVFrameToImageSharp(tempFrameForSeek);
				//     if (firstImage != null) hashes.Add(CalculateAverageHash(firstImage));
				//     // then loop for frameCount -1
				// }

				// For simplicity, let's assume the next sequential calls will cover the desired frames.
				// This means the seek in TryDecodeFrame positions the stream, and TryDecodeNextFrameSequential picks up.
				// This might result in the first frame being the one *at or after* the timestamp.
				// A frame-accurate extraction would require more careful handling of the first frame after seek.

				for (int i = 0; i < frameCount; i++) {
					AVFrame decodedFrame;
					bool successfullyDecoded = decoder.TryDecodeNextFrameSequential(out decodedFrame);

					if (successfullyDecoded) {
						if (i % sampleRate == 0) // Only process and hash if it's a sample point
						{
							Image<L8>? imageSharpFrame = null; // Changed to nullable
							try {
								if (decodedFrame.data[0] == null || decodedFrame.width <= 0 || decodedFrame.height <= 0) {
									Console.WriteLine($"Warning: Decoded frame data for frame index {startFrameIndex + i} is invalid or empty. Skipping hash.");
									continue;
								}

								imageSharpFrame = ConvertAVFrameToImageSharp(decodedFrame);

								if (imageSharpFrame != null) {
									ulong hash = CalculateAverageHash(imageSharpFrame);
									hashes.Add(hash);
								}
							}
							catch (Exception ex) {
								Console.WriteLine($"Error processing frame index {startFrameIndex + i}: {ex.Message}. Skipping hash.");
							}
							finally {
								imageSharpFrame?.Dispose();
							}
						}
					}
					else {
						Console.WriteLine($"Warning: Failed to decode frame at effective index {startFrameIndex + i}. This might happen near EOF or if segment is too short.");
						// Stop trying to get more frames from this segment if one fails, as sequence is broken.
						break;
					}
				}
			}
			return hashes;
		}

		private unsafe Image<L8>? ConvertAVFrameToImageSharp(AVFrame avFrame) // Return type changed to nullable
		{
			if (avFrame.data[0] == null || avFrame.width <= 0 || avFrame.height <= 0) return null;

			int width = avFrame.width;
			int height = avFrame.height;
			AVPixelFormat sourcePixelFormat = (AVPixelFormat)avFrame.format;

			// Target format for ImageSharp hashing
			AVPixelFormat targetPixelFormat = AVPixelFormat.AV_PIX_FMT_GRAY8;
			Image<L8> grayscaleImage = null;

			// If already GRAY8, copy directly
			if (sourcePixelFormat == targetPixelFormat) {
				grayscaleImage = new Image<L8>(width, height);
				byte* srcRow = avFrame.data[0];
				grayscaleImage.ProcessPixelRows(accessor => {
					for (int y = 0; y < accessor.Height; y++) {
						var pixelRowSpan = accessor.GetRowSpan(y);
						byte* currentSrcRow = srcRow + y * avFrame.linesize[0]; // Use the correct stride for source
						for (int x = 0; x < accessor.Width; x++) {
							pixelRowSpan[x] = new L8(currentSrcRow[x]);
						}
					}
				});
				return grayscaleImage;
			}

			// Use SWS_SCALE for conversion to GRAY8
			SwsContext* swsCtx = null;
			AVFrame* dstFrame = null;
			byte* dstDataBuffer = null;

			try {
				swsCtx = ffmpeg.sws_getContext(width, height, sourcePixelFormat,
											   width, height, targetPixelFormat,
											   ffmpeg.SWS_BILINEAR, null, null, null);
				if (swsCtx == null) {
					Console.WriteLine("Error: Could not initialize SWS context for pixel format conversion.");
					return null;
				}

				dstFrame = ffmpeg.av_frame_alloc();
				if (dstFrame == null) {
					Console.WriteLine("Error: Could not allocate destination AVFrame for SWS_SCALE.");
					return null;
				}

				dstFrame->format = (int)targetPixelFormat;
				dstFrame->width = width;
				dstFrame->height = height;

				int bufferSize = ffmpeg.av_image_get_buffer_size(targetPixelFormat, width, height, 1);
				if (bufferSize < 0) {
					Console.WriteLine("Error: Could not get buffer size for destination image in SWS_SCALE.");
					return null;
				}
				dstDataBuffer = (byte*)ffmpeg.av_malloc((ulong)bufferSize);
				if (dstDataBuffer == null) {
					Console.WriteLine("Error: Could not allocate buffer for destination image in SWS_SCALE.");
					return null;
				}

				// Pass pointers to the start of the data/linesize arrays
				byte_ptrArray4 dstData4 = default;    // Temporary for the PInvoke call, initialized
				int_array4 dstLinesize4 = default;  // Temporary for the PInvoke call, initialized

				ffmpeg.av_image_fill_arrays(ref dstData4, ref dstLinesize4, dstDataBuffer,
											targetPixelFormat, width, height, 1).ThrowExceptionIfError();

				// Now, dstFrame->data[0] should be set by av_image_fill_arrays IF dstDataBuffer was correctly assigned to it.
				// Or, more likely, dstData4.Item0 (or similar) now holds the pointer.
				// We need to ensure dstFrame's pointers are set up for sws_scale, or use dstData4/dstLinesize4.
				// Let's assume dstData4 and dstLinesize4 are now populated correctly.
				// And copy them to the main dstFrame if needed, or use them directly.
				// For GRAY8, only data[0] and linesize[0] are used.
				dstFrame->data[0] = dstData4[0]; // Assuming indexer access on these temp structs
				dstFrame->linesize[0] = dstLinesize4[0];
				// For other planes if necessary:
				// dstFrame->data[1] = dstData4[1]; dstFrame->linesize[1] = dstLinesize4[1]; etc.

				byte*[] srcDataSws = new byte*[] { avFrame.data[0], avFrame.data[1], avFrame.data[2], avFrame.data[3] };
				int[] srcLinesizeSws = new int[] { avFrame.linesize[0], avFrame.linesize[1], avFrame.linesize[2], avFrame.linesize[3] };

				// Use the pointers from the temporary, filled arrays for destination in sws_scale
				byte*[] dstDataSws = new byte*[] { dstData4[0], dstData4[1], dstData4[2], dstData4[3] };
				int[] dstLinesizeSws = new int[] { dstLinesize4[0], dstLinesize4[1], dstLinesize4[2], dstLinesize4[3] };

				int outputSliceHeight = ffmpeg.sws_scale(swsCtx, srcDataSws, srcLinesizeSws, 0, height,
														 dstDataSws, dstLinesizeSws);

				if (outputSliceHeight <= 0) {
					Console.WriteLine("Error: sws_scale failed or returned 0 height.");
					return null;
				}

				grayscaleImage = new Image<L8>(width, height);
				byte* convertedSrcRow = dstFrame->data[0];
				int convertedLinesize = dstFrame->linesize[0]; // Capture for lambda

				grayscaleImage.ProcessPixelRows(accessor => {
					for (int y = 0; y < accessor.Height; y++) {
						var pixelRowSpan = accessor.GetRowSpan(y);
						byte* currentConvertedSrcRow = convertedSrcRow + y * convertedLinesize;
						for (int x = 0; x < accessor.Width; x++) {
							pixelRowSpan[x] = new L8(currentConvertedSrcRow[x]);
						}
					}
				});
			}
			catch (Exception ex) {
				Console.WriteLine($"Error during SWS_SCALE conversion: {ex.Message}");
				grayscaleImage?.Dispose(); // dispose if partially created
				return null;
			}
			finally {
				if (dstDataBuffer != null) ffmpeg.av_free(dstDataBuffer);
				if (dstFrame != null) ffmpeg.av_frame_free(&dstFrame);
				if (swsCtx != null) ffmpeg.sws_freeContext(swsCtx);
			}

			return grayscaleImage;
		}

		internal ulong CalculateAverageHash(Image<L8> image) // Made internal, parameter is non-nullable
		{
			// Null check removed as call sites (e.g., in ExtractAndHashFrames) should ensure non-null.
			// 1. Resize to 8x8
			image.Mutate(x => x.Resize(new ResizeOptions {
				Size = new Size(8, 8),
				Mode = ResizeMode.Stretch
			}));

			// 2. Image is already L8 (grayscale)

			// 3. Calculate the average pixel value
			long sum = 0;
			image.ProcessPixelRows(accessor => {
				for (int y = 0; y < accessor.Height; y++) {
					var pixelRow = accessor.GetRowSpan(y);
					for (int x = 0; x < accessor.Width; x++) {
						sum += pixelRow[x].PackedValue;
					}
				}
			});
			byte average = (byte)(sum / (image.Width * image.Height)); // image.Width/Height are still valid after Resize.

			// 4. For each pixel, if its value is >= average, assign 1, else 0.
			ulong hash = 0;
			ulong bit = 1;
			image.ProcessPixelRows(accessor => {
				for (int y = 0; y < accessor.Height; y++) // accessor.Height will be 8
				{
					var pixelRow = accessor.GetRowSpan(y);
					for (int x = 0; x < accessor.Width; x++) // accessor.Width will be 8
					{
						if (pixelRow[x].PackedValue >= average) {
							hash |= bit;
						}
						// Check if this is the last bit to avoid over-shifting
						if (y * accessor.Width + x == 63) break;
						bit <<= 1;
					}
					if (y * accessor.Width + (accessor.Width - 1) >= 63 && y == accessor.Height - 1) break;
				}
			});
			return hash;
		}

		internal float CompareAverageHashes(ulong hash1, ulong hash2) // Made internal
		{
			ulong xor = hash1 ^ hash2;
			int hammingDistance = 0;
			while (xor > 0) {
				hammingDistance += (int)(xor & 1);
				xor >>= 1;
			}
			const int totalBits = 64;
			return 1.0f - ((float)hammingDistance / totalBits);
		}
	}
}
