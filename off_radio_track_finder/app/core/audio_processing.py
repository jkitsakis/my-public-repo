from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from mutagen.id3 import ID3, ID3NoHeaderError, TXXX

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


PROCESSING_TAG = "OFFRADIO_SPEAKER_SAFE_VERSION"
PROCESSING_VERSION = "12"

_MP3_BITRATES_KBPS = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)


@dataclass(frozen=True)
class SpeakerSafeSettings:
    """Universal, transparent professional MP3 mastering settings.

    Design goals:
      * normalize programme loudness without changing the tonal balance;
      * protect the final encoded MP3 from true-peak overs;
      * encode MP3 only once;
      * verify the encoded result and retry from a lossless intermediate;
      * preserve ID3 metadata and artwork.
    """

    highpass_hz: int = 30
    bass_frequency_hz: int = 90
    bass_gain_db: float = 0.0
    bass_q: float = 0.85
    target_lufs: float = -14.0
    true_peak_db: float = -1.5
    mp3_bitrate: str = "320k"
    sample_rate_hz: int = 44100
    preserve_source_stream_quality: bool = True
    loudness_tolerance_lu: float = 1.0

    adaptive_bass_enabled: bool = False
    bass_detection_low_hz: int = 45
    bass_detection_high_hz: int = 125
    bass_reference_low_hz: int = 180
    bass_reference_high_hz: int = 1200
    bass_excess_threshold_db: float = 5.0
    max_adaptive_bass_reduction_db: float = 4.0

    dynamic_bass_enabled: bool = False
    dynamic_bass_crossover_hz: int = 130
    dynamic_bass_threshold_dbfs: float = -24.0
    dynamic_bass_ratio: float = 4.0
    dynamic_bass_attack_ms: float = 12.0
    dynamic_bass_release_ms: float = 260.0
    dynamic_bass_knee: float = 3.0

    allow_loudness_boost: bool = True
    mp3_peak_safety_margin_db: float = 0.6
    max_peak_retries: int = 2

    def validate(self) -> None:
        if not 0 <= self.highpass_hz <= 80:
            raise ValueError("highpass_hz must be between 0 and 80")
        if not 40 <= self.bass_frequency_hz <= 250:
            raise ValueError("bass_frequency_hz must be between 40 and 250")
        if not -12.0 <= self.bass_gain_db <= 0.0:
            raise ValueError("bass_gain_db must be between -12 and 0 dB")
        if not 0.2 <= self.bass_q <= 4.0:
            raise ValueError("bass_q must be between 0.2 and 4.0")
        if not -30.0 <= self.target_lufs <= -10.0:
            raise ValueError("target_lufs must be between -30 and -10")
        if not -6.0 <= self.true_peak_db <= -0.5:
            raise ValueError("true_peak_db must be between -6 and -0.5")
        match = re.fullmatch(r"(\d{2,3})k", self.mp3_bitrate)
        if not match or not 32 <= int(match.group(1)) <= 320:
            raise ValueError("mp3_bitrate must be between 32k and 320k")
        if self.sample_rate_hz not in {32000, 44100, 48000}:
            raise ValueError("sample_rate_hz must be 32000, 44100, or 48000")
        if not 0.0 <= self.loudness_tolerance_lu <= 3.0:
            raise ValueError("loudness_tolerance_lu must be between 0 and 3")
        if not 20 <= self.bass_detection_low_hz < self.bass_detection_high_hz <= 250:
            raise ValueError("invalid bass detection range")
        if not 80 <= self.bass_reference_low_hz < self.bass_reference_high_hz <= 4000:
            raise ValueError("invalid bass reference range")
        if not 0.0 <= self.bass_excess_threshold_db <= 30.0:
            raise ValueError("bass_excess_threshold_db must be between 0 and 30")
        if not 0.0 <= self.max_adaptive_bass_reduction_db <= 12.0:
            raise ValueError("max_adaptive_bass_reduction_db must be between 0 and 12")
        if not 80 <= self.dynamic_bass_crossover_hz <= 180:
            raise ValueError("dynamic_bass_crossover_hz must be between 80 and 180")
        if not -60.0 <= self.dynamic_bass_threshold_dbfs <= -3.0:
            raise ValueError("dynamic_bass_threshold_dbfs must be between -60 and -3")
        if not 1.0 <= self.dynamic_bass_ratio <= 20.0:
            raise ValueError("dynamic_bass_ratio must be between 1 and 20")
        if not 0.1 <= self.dynamic_bass_attack_ms <= 200.0:
            raise ValueError("dynamic_bass_attack_ms must be between 0.1 and 200")
        if not 20.0 <= self.dynamic_bass_release_ms <= 2000.0:
            raise ValueError("dynamic_bass_release_ms must be between 20 and 2000")
        if not 1.0 <= self.dynamic_bass_knee <= 8.0:
            raise ValueError("dynamic_bass_knee must be between 1 and 8")
        if not 0.2 <= self.mp3_peak_safety_margin_db <= 2.0:
            raise ValueError("mp3_peak_safety_margin_db must be between 0.2 and 2")
        if not 0 <= self.max_peak_retries <= 4:
            raise ValueError("max_peak_retries must be between 0 and 4")


@dataclass(frozen=True)
class LoudnessMeasurement:
    integrated_lufs: float
    loudness_range: float
    true_peak_db: float
    threshold_lufs: float
    offset_db: float


@dataclass(frozen=True)
class BassMeasurement:
    bass_percentile_db: float
    reference_percentile_db: float
    excess_percentile_db: float
    recommended_reduction_db: float


@dataclass(frozen=True)
class AudioStreamQuality:
    """Encoding properties used for the repaired MP3 stream."""

    bitrate: str
    sample_rate_hz: int
    channels: int


@dataclass(frozen=True)
class SpeakerSafeResult:
    processed: bool
    skipped_reason: str = ""
    before: LoudnessMeasurement | None = None
    after: LoudnessMeasurement | None = None
    bass: BassMeasurement | None = None
    backup_file: Path | None = None


def _run(command: list[str], error_message: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(diagnostic[-4000:] or error_message)
    return completed


def _nearest_mp3_bitrate(bitrate_bps: int) -> str:
    """Map an average source bitrate to the nearest legal MP3 bitrate."""
    bitrate_kbps = max(1, round(bitrate_bps / 1000))
    selected = min(
        _MP3_BITRATES_KBPS,
        key=lambda candidate: (abs(candidate - bitrate_kbps), candidate),
    )
    return f"{selected}k"


def _ffprobe_for(ffmpeg: Path) -> str:
    sibling = ffmpeg.with_name(
        "ffprobe.exe" if ffmpeg.suffix.casefold() == ".exe" else "ffprobe"
    )
    if sibling.is_file():
        return str(sibling)
    discovered = shutil.which("ffprobe")
    if discovered:
        return discovered
    raise FileNotFoundError("FFprobe was not found next to FFmpeg or on PATH")


def probe_audio_stream_quality(
    ffmpeg: Path,
    source: Path,
    settings: SpeakerSafeSettings,
) -> AudioStreamQuality:
    """Read the first audio stream and choose matching MP3 output properties.

    For example, a 160 kb/s, 44.1 kHz stereo source is repaired as a
    160 kb/s, 44.1 kHz stereo MP3. Raising it to 320 kb/s would only increase
    file size; it cannot recreate information missing from the source stream.
    """
    if not settings.preserve_source_stream_quality:
        return AudioStreamQuality(
            settings.mp3_bitrate,
            settings.sample_rate_hz,
            2,
        )

    command = [
        _ffprobe_for(ffmpeg),
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=bit_rate,sample_rate,channels:format=bit_rate",
        "-of", "json",
        str(source),
    ]
    completed = _run(command, "Audio stream quality analysis failed")
    try:
        payload = json.loads(completed.stdout)
        streams = payload.get("streams") or []
        stream = streams[0]
        sample_rate_hz = int(stream["sample_rate"])
        channels = int(stream["channels"])
        bitrate_bps = int(
            stream.get("bit_rate")
            or (payload.get("format") or {}).get("bit_rate")
        )
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"FFprobe returned incomplete stream quality data for {source.name}"
        ) from exc

    # libmp3lame supports mono or stereo. MP3 inputs normally contain one or
    # two channels; defensively downmix unusual multichannel input to stereo.
    channels = 1 if channels == 1 else 2
    if sample_rate_hz not in {32000, 44100, 48000}:
        sample_rate_hz = min(
            (32000, 44100, 48000),
            key=lambda candidate: abs(candidate - sample_rate_hz),
        )

    return AudioStreamQuality(
        bitrate=_nearest_mp3_bitrate(bitrate_bps),
        sample_rate_hz=sample_rate_hz,
        channels=channels,
    )


def _extract_loudnorm_json(stderr: str) -> dict[str, object]:
    """Extract the final loudnorm JSON object from FFmpeg stderr.

    FFmpeg writes filter output to stderr together with ordinary diagnostics.
    Different FFmpeg versions may vary whitespace and surrounding log lines,
    so parsing the entire stderr string or relying on one strict regex is
    fragile.
    """
    decoder = json.JSONDecoder()
    candidates: list[dict[str, object]] = []

    for match in re.finditer(r"\{", stderr):
        try:
            value, _ = decoder.raw_decode(stderr[match.start():])
        except json.JSONDecodeError:
            continue

        if isinstance(value, dict) and {
            "input_i",
            "input_lra",
            "input_tp",
            "input_thresh",
        }.issubset(value):
            candidates.append(value)

    if candidates:
        return candidates[-1]

    diagnostic = stderr.strip()
    if len(diagnostic) > 4000:
        diagnostic = diagnostic[-4000:]

    raise RuntimeError(
        "FFmpeg loudness analysis returned no usable JSON result."
        + (f"\nFFmpeg output:\n{diagnostic}" if diagnostic else "")
    )


def _finite_loudnorm_value(
    data: dict[str, object],
    key: str,
    *,
    source: Path,
    default: float | None = None,
) -> float:
    raw = data.get(key, default)

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"FFmpeg returned an invalid loudness value for {key}: {raw!r} "
            f"({source.name})"
        ) from exc

    if not math.isfinite(value):
        raise RuntimeError(
            f"FFmpeg returned {key}={raw!r} for {source.name}. "
            "The audio may be silent, empty, damaged, or too short."
        )

    return value


def measure_loudness(
    ffmpeg: Path,
    source: Path,
    *,
    target_lufs: float,
    true_peak_db: float,
) -> LoudnessMeasurement:
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        (
            f"loudnorm=I={target_lufs:.2f}:"
            f"LRA=11:"
            f"TP={true_peak_db:.2f}:"
            "print_format=json"
        ),
        "-f",
        "null",
        "-",
    ]

    try:
        completed = _run(command, "Loudness analysis failed")
        data = _extract_loudnorm_json(completed.stderr or "")
    except Exception as exc:
        raise RuntimeError(
            f"Loudness analysis failed for {source.name}: {exc}"
        ) from exc

    return LoudnessMeasurement(
        integrated_lufs=_finite_loudnorm_value(
            data, "input_i", source=source
        ),
        loudness_range=_finite_loudnorm_value(
            data, "input_lra", source=source
        ),
        true_peak_db=_finite_loudnorm_value(
            data, "input_tp", source=source
        ),
        threshold_lufs=_finite_loudnorm_value(
            data, "input_thresh", source=source
        ),
        offset_db=_finite_loudnorm_value(
            data, "target_offset", source=source, default=0.0
        ),
    )


def _band_rms_series(ffmpeg: Path, source: Path, chain: str) -> list[float]:
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(source),
        "-vn", "-af",
        f"{chain},aformat=channel_layouts=mono,"
        "asetnsamples=n=4410:p=1,astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null", "-",
    ]
    completed = _run(command, "Band analysis failed")
    raw = re.findall(
        r"lavfi\.astats\.Overall\.RMS_level=(-?inf|[-+]?\d+(?:\.\d+)?)",
        completed.stderr,
    )
    values = [-120.0 if value == "-inf" else float(value) for value in raw]
    if not values:
        raise RuntimeError("FFmpeg returned no band-analysis frames")
    return values


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def measure_bass_balance(
    ffmpeg: Path,
    source: Path,
    settings: SpeakerSafeSettings,
) -> BassMeasurement:
    bass = _band_rms_series(
        ffmpeg,
        source,
        f"highpass=f={settings.bass_detection_low_hz}:p=2,"
        f"lowpass=f={settings.bass_detection_high_hz}:p=2",
    )
    reference = _band_rms_series(
        ffmpeg,
        source,
        f"highpass=f={settings.bass_reference_low_hz}:p=2,"
        f"lowpass=f={settings.bass_reference_high_hz}:p=2",
    )
    count = min(len(bass), len(reference))
    paired = [
        bass[index] - reference[index]
        for index in range(count)
        if bass[index] > -90.0 and reference[index] > -90.0
    ]
    if not paired:
        paired = [0.0]
    excess = _percentile(paired, 0.95)
    over = max(0.0, excess - settings.bass_excess_threshold_db)
    # Static EQ is intentionally modest; the dynamic compressor handles bursts.
    reduction = -min(settings.max_adaptive_bass_reduction_db, over * 0.65)
    return BassMeasurement(
        bass_percentile_db=_percentile(bass, 0.95),
        reference_percentile_db=_percentile(reference, 0.95),
        excess_percentile_db=excess,
        recommended_reduction_db=reduction,
    )


def _read_processing_version(source: Path) -> str:
    try:
        tags = ID3(str(source))
        frames = tags.getall(f"TXXX:{PROCESSING_TAG}")
        return str(frames[0].text[0]).strip() if frames and frames[0].text else ""
    except Exception:
        return ""


def _processing_signature(
    settings: SpeakerSafeSettings,
    preserve_original_loudness: bool,
) -> str:
    payload = asdict(settings)
    payload["preserve_original_loudness"] = bool(preserve_original_loudness)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _read_processing_signature(source: Path) -> str:
    try:
        tags = ID3(str(source))
        frames = tags.getall("TXXX:OFFRADIO_SPEAKER_SAFE_SETTINGS")
        return str(frames[0].text[0]).strip() if frames and frames[0].text else ""
    except Exception:
        return ""


def _copy_id3(source: Path, destination: Path) -> None:
    try:
        tags = ID3(str(source))
    except ID3NoHeaderError:
        return
    tags.save(str(destination), v2_version=3)


def _write_processing_tags(
    source: Path,
    settings: SpeakerSafeSettings,
    preserve_original_loudness: bool,
) -> None:
    try:
        tags = ID3(str(source))
    except ID3NoHeaderError:
        tags = ID3()
    for description in (PROCESSING_TAG, "OFFRADIO_SPEAKER_SAFE_SETTINGS"):
        tags.delall(f"TXXX:{description}")
    tags.add(TXXX(encoding=3, desc=PROCESSING_TAG, text=[PROCESSING_VERSION]))
    tags.add(
        TXXX(
            encoding=3,
            desc="OFFRADIO_SPEAKER_SAFE_SETTINGS",
            text=[_processing_signature(settings, preserve_original_loudness)],
        )
    )
    tags.save(str(source), v2_version=3)


def _premaster_graph(
    settings: SpeakerSafeSettings,
    static_bass_gain_db: float,
    channels: int,
) -> str:
    input_filters: list[str] = []
    if settings.highpass_hz > 0:
        input_filters.append(f"highpass=f={settings.highpass_hz}:p=2")
    channel_layout = "mono" if channels == 1 else "stereo"
    input_filters.append(
        f"aformat=sample_fmts=fltp:channel_layouts={channel_layout}"
    )
    prefix = ",".join(input_filters)

    if not settings.dynamic_bass_enabled:
        tail = ""
        if abs(static_bass_gain_db) >= 0.01:
            tail = (
                f",equalizer=f={settings.bass_frequency_hz}:t=q:"
                f"w={settings.bass_q:.2f}:g={static_bass_gain_db:.2f}"
            )
        return f"[0:a:0]{prefix}{tail}[premaster]"

    threshold_linear = 10.0 ** (settings.dynamic_bass_threshold_dbfs / 20.0)
    low_tail = ""
    if abs(static_bass_gain_db) >= 0.01:
        low_tail = f",volume={static_bass_gain_db:.2f}dB"

    return (
        f"[0:a:0]{prefix},"
        f"acrossover=split={settings.dynamic_bass_crossover_hz}:order=8th"
        "[low][high];"
        f"[low]acompressor=threshold={threshold_linear:.6f}:"
        f"ratio={settings.dynamic_bass_ratio:.2f}:"
        f"attack={settings.dynamic_bass_attack_ms:.2f}:"
        f"release={settings.dynamic_bass_release_ms:.2f}:"
        f"knee={settings.dynamic_bass_knee:.2f}:"
        "makeup=1:link=maximum:detection=rms:mix=1"
        f"{low_tail}[low_controlled];"
        "[low_controlled][high]amix=inputs=2:normalize=0:dropout_transition=0"
        "[premaster]"
    )


def _render_lossless_premaster(
    ffmpeg: Path,
    source: Path,
    destination: Path,
    settings: SpeakerSafeSettings,
    static_bass_gain_db: float,
    stream_quality: AudioStreamQuality,
) -> None:
    graph = _premaster_graph(settings, static_bass_gain_db, stream_quality.channels)
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-i", str(source),
        "-filter_complex", graph,
        "-map", "[premaster]", "-vn",
        "-c:a", "flac", "-compression_level", "5",
        "-ar", str(stream_quality.sample_rate_hz),
        "-ac", str(stream_quality.channels),
        str(destination),
    ]
    _run(command, "Lossless pre-master render failed")
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise RuntimeError("FFmpeg produced no lossless pre-master")


def _master_filter(
    measurement: LoudnessMeasurement,
    settings: SpeakerSafeSettings,
    target_lufs: float,
    extra_attenuation_db: float,
) -> tuple[str, str]:
    internal_peak_target = settings.true_peak_db - settings.mp3_peak_safety_margin_db
    delta = measurement.integrated_lufs - target_lufs

    # target_lufs has already been capped against the source loudness when
    # loudness boosting is disabled. Therefore normalization may safely restore
    # level lost during bass control without making a naturally quiet source
    # louder than it originally was.
    should_normalize = abs(delta) > settings.loudness_tolerance_lu

    filters: list[str] = []
    mode = "level unchanged"
    if should_normalize:
        filters.append(
            f"loudnorm=I={target_lufs:.2f}:LRA=11:TP={internal_peak_target:.2f}:"
            f"measured_I={measurement.integrated_lufs:.2f}:"
            f"measured_LRA={measurement.loudness_range:.2f}:"
            f"measured_TP={measurement.true_peak_db:.2f}:"
            f"measured_thresh={measurement.threshold_lufs:.2f}:"
            f"offset={measurement.offset_db:.2f}:linear=true:print_format=summary"
        )
        mode = "two-pass loudness normalization"
    elif measurement.true_peak_db > internal_peak_target:
        # Loudness is already close to target, but short peaks are unsafe.
        # A fixed volume reduction would unnecessarily lower integrated
        # loudness (for example from about -14 LUFS to -16 LUFS). Use
        # loudnorm's dynamic true-peak stage instead, so only the transient
        # excess is controlled while average loudness stays near target.
        filters.append(
            f"loudnorm=I={target_lufs:.2f}:LRA=11:TP={internal_peak_target:.2f}:"
            f"measured_I={measurement.integrated_lufs:.2f}:"
            f"measured_LRA={measurement.loudness_range:.2f}:"
            f"measured_TP={measurement.true_peak_db:.2f}:"
            f"measured_thresh={measurement.threshold_lufs:.2f}:"
            f"offset={measurement.offset_db:.2f}:linear=false:print_format=summary"
        )
        mode = "dynamic true-peak loudness control"

    if extra_attenuation_db < -0.001:
        filters.append(f"volume={extra_attenuation_db:.3f}dB")
        mode += f" + retry attenuation {extra_attenuation_db:.2f} dB"

    return (",".join(filters) if filters else "anull"), mode


def _encode_mp3(
    ffmpeg: Path,
    premaster: Path,
    destination: Path,
    audio_filter: str,
    settings: SpeakerSafeSettings,
    stream_quality: AudioStreamQuality,
) -> None:
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-i", str(premaster),
        "-map", "0:a:0", "-vn", "-af", audio_filter,
        "-c:a", "libmp3lame", "-b:a", stream_quality.bitrate,
        "-ar", str(stream_quality.sample_rate_hz),
        "-ac", str(stream_quality.channels),
        "-id3v2_version", "3", "-write_id3v1", "1",
        str(destination),
    ]
    _run(command, "Final MP3 encoding failed")
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise RuntimeError("FFmpeg produced no final MP3")


def apply_speaker_safe_processing(
    audio_file: Path,
    ffmpeg: Path,
    *,
    settings: SpeakerSafeSettings | None = None,
    create_backup: bool = False,
    force: bool = False,
    preserve_original_loudness: bool = False,
    log: LogFn = _noop,
) -> SpeakerSafeResult:
    """Create a verified speaker-safe master and replace the MP3 atomically."""
    source = audio_file.expanduser().resolve()
    ffmpeg = ffmpeg.expanduser().resolve()
    settings = settings or SpeakerSafeSettings()
    settings.validate()

    if not source.is_file() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"MP3 file not found or empty: {source}")
    if not ffmpeg.is_file():
        raise FileNotFoundError(f"FFmpeg not found: {ffmpeg}")
    stream_quality = probe_audio_stream_quality(ffmpeg, source, settings)
    log(
        f"Source stream: {source.name} | {stream_quality.bitrate} | "
        f"{stream_quality.sample_rate_hz} Hz | "
        f"{stream_quality.channels} channel(s) | preserved"
    )
    if (
        not force
        and _read_processing_version(source) == PROCESSING_VERSION
        and _read_processing_signature(source)
        == _processing_signature(settings, preserve_original_loudness)
    ):
        return SpeakerSafeResult(False, "already processed with identical settings")

    before = measure_loudness(
        ffmpeg, source,
        target_lufs=settings.target_lufs,
        true_peak_db=settings.true_peak_db,
    )
    if preserve_original_loudness:
        target_lufs = max(-30.0, min(-10.0, before.integrated_lufs))
    elif settings.allow_loudness_boost:
        target_lufs = settings.target_lufs
    else:
        # Never make a naturally quiet source louder. For a loud source, allow
        # the final master to recover level lost by the bass-control stage, up
        # to the configured target.
        target_lufs = min(settings.target_lufs, before.integrated_lufs)

    bass: BassMeasurement | None = None
    adaptive_reduction = 0.0
    if settings.adaptive_bass_enabled:
        bass = measure_bass_balance(ffmpeg, source, settings)
        adaptive_reduction = bass.recommended_reduction_db
        log(
            f"Bass analysis: {source.name} | "
            f"P95 bass={bass.bass_percentile_db:.2f} dBFS | "
            f"P95 reference={bass.reference_percentile_db:.2f} dBFS | "
            f"excess={bass.excess_percentile_db:+.2f} dB | "
            f"static low-band trim={adaptive_reduction:.2f} dB"
        )

    static_bass_gain = max(-12.0, settings.bass_gain_db + adaptive_reduction)
    backup_file: Path | None = None

    with tempfile.TemporaryDirectory(prefix="offradio_master_", dir=source.parent) as directory:
        work = Path(directory)
        premaster = work / "premaster.flac"
        candidate = work / "candidate.mp3"

        _render_lossless_premaster(
            ffmpeg,
            source,
            premaster,
            settings,
            static_bass_gain,
            stream_quality,
        )
        premaster_measurement = measure_loudness(
            ffmpeg, premaster,
            target_lufs=target_lufs,
            true_peak_db=settings.true_peak_db,
        )

        log(
            f"Pre-master: {source.name} | "
            f"LUFS={premaster_measurement.integrated_lufs:.2f} | "
            f"TP={premaster_measurement.true_peak_db:.2f} dBTP | "
            f"dynamic bass={'on' if settings.dynamic_bass_enabled else 'off'}"
        )

        after: LoudnessMeasurement | None = None
        final_mode = ""
        extra_attenuation = 0.0

        for attempt in range(settings.max_peak_retries + 1):
            audio_filter, final_mode = _master_filter(
                premaster_measurement,
                settings,
                target_lufs,
                extra_attenuation,
            )
            _encode_mp3(
                ffmpeg,
                premaster,
                candidate,
                audio_filter,
                settings,
                stream_quality,
            )
            after = measure_loudness(
                ffmpeg, candidate,
                target_lufs=target_lufs,
                true_peak_db=settings.true_peak_db,
            )
            if after.true_peak_db <= settings.true_peak_db + 0.03:
                break

            required = settings.true_peak_db - after.true_peak_db - 0.12
            extra_attenuation += required
            log(
                f"Encoded peak retry {attempt + 1}: {source.name} | "
                f"TP={after.true_peak_db:.2f} dBTP | "
                f"next attenuation={extra_attenuation:.2f} dB"
            )
        else:
            raise RuntimeError("Could not create a true-peak-safe MP3")

        assert after is not None
        if after.true_peak_db > settings.true_peak_db + 0.03:
            raise RuntimeError(
                f"Output true peak {after.true_peak_db:.2f} dBTP exceeds safe limit"
            )

        _copy_id3(source, candidate)
        _write_processing_tags(candidate, settings, preserve_original_loudness)

        if create_backup:
            backup_file = source.with_name(
                f"{source.stem}.before_speaker_safe{source.suffix}"
            )
            if not backup_file.exists():
                shutil.copy2(source, backup_file)

        os.replace(candidate, source)

    log(
        f"Professional master: {source.name} | "
        f"{before.integrated_lufs:.2f} -> {after.integrated_lufs:.2f} LUFS | "
        f"TP {after.true_peak_db:.2f} dBTP | "
        f"{stream_quality.bitrate}/{stream_quality.sample_rate_hz}Hz | "
        f"bass trim {static_bass_gain:.2f} dB | {final_mode}"
    )
    return SpeakerSafeResult(
        True,
        before=before,
        after=after,
        bass=bass,
        backup_file=backup_file,
    )