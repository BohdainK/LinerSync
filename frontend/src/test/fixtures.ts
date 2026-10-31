import type {
  AttentionOut,
  BackupPlaylistOut,
  BackupRunOut,
  BackupServiceOut,
  BackupSummaryOut,
  ChangeOut,
  ConnectionOut,
  DestinationOut,
  HomeOut,
  JobOut,
  PlaylistOut,
  RunOut,
  RunSummaryOut,
  SettingsOut,
  SetupGuide,
  StatsOut,
  SyncOut,
  TrackOut,
} from "@/api/types";

const T0 = "2026-01-01T12:00:00Z";

export function jobOut(o: Partial<JobOut> = {}): JobOut {
  return {
    job_id: "job-1",
    kind: "sync",
    status: "running",
    label: "Sync Road trip",
    sync_id: "sync-1",
    created_at: T0,
    started_at: T0,
    finished_at: null,
    run_ids: [],
    backup_ids: [],
    error: null,
    progress: null,
    cancel_requested: false,
    ...o,
  };
}

export function connectionOut(o: Partial<ConnectionOut> = {}): ConnectionOut {
  return {
    service: "spotify",
    display_name: "Spotify",
    category: "streaming",
    capabilities: ["read_order", "add", "remove", "reorder", "edit_metadata", "create_playlist", "delete_playlist"],
    experimental: false,
    status: "connected",
    message: "",
    account_name: "Sam",
    checked_at: T0,
    login_expires_at: null,
    login_expiry_estimated: false,
    values: {},
    secrets_set: [],
    ...o,
  };
}

export function destinationOut(o: Partial<DestinationOut> = {}): DestinationOut {
  return {
    member_id: "member-2",
    service: "tidal",
    playlist_id: "pl-tidal",
    mode: "full",
    effective_mode: "full",
    limitations: [],
    last_synced_at: T0,
    missing: false,
    ...o,
  };
}

export function runSummaryOut(o: Partial<RunSummaryOut> = {}): RunSummaryOut {
  return {
    run_id: "run-1",
    sync_id: "sync-1",
    kind: "sync",
    trigger: "manual",
    dry_run: false,
    status: "succeeded",
    started_at: T0,
    finished_at: T0,
    error: null,
    added: 0,
    removed: 0,
    not_made: 0,
    unmatched: 0,
    ...o,
  };
}

export function runOut(o: Partial<RunOut> = {}): RunOut {
  return {
    ...runSummaryOut(),
    members: [],
    unresolved: [],
    undoes_run_id: null,
    undoable: false,
    ...o,
  };
}

export function syncOut(o: Partial<SyncOut> = {}): SyncOut {
  return {
    sync_id: "sync-1",
    name: "Road trip",
    status: "active",
    pause_reason: null,
    schedule: null,
    effective_schedule: "0 * * * *",
    next_run_at: null,
    source: { member_id: "member-1", service: "spotify", playlist_id: "pl-spotify" },
    destinations: [destinationOut()],
    active_job: null,
    last_run: null,
    ...o,
  };
}

export function trackOut(o: Partial<TrackOut> = {}): TrackOut {
  return {
    service: "spotify",
    track_id: "track-1",
    title: "Song",
    artists: ["Artist"],
    album: "Album",
    duration_ms: 215_000,
    isrc: null,
    url: null,
    known: true,
    ...o,
  };
}

export function attentionOut(o: Partial<AttentionOut> = {}): AttentionOut {
  return {
    item_id: "att-1",
    kind: "unmatched",
    created_at: T0,
    detail: "",
    sync_id: "sync-1",
    sync_name: "Road trip",
    member_id: "member-2",
    service: "tidal",
    source: trackOut(),
    target_service: "tidal",
    candidates: [],
    not_made: [],
    ...o,
  };
}

export function homeOut(o: Partial<HomeOut> = {}): HomeOut {
  return {
    running: [],
    queued: [],
    next_up: [],
    recent: [],
    attention_count: 0,
    warnings: [],
    api_limits: [],
    ...o,
  };
}

export function playlistOut(o: Partial<PlaylistOut> = {}): PlaylistOut {
  return {
    service: "spotify",
    playlist_id: "pl-1",
    name: "Road trip",
    description: "",
    cover_url: null,
    owned: true,
    readable: true,
    unreadable_reason: null,
    track_count: 12,
    ...o,
  };
}

export function backupPlaylistOut(o: Partial<BackupPlaylistOut> = {}): BackupPlaylistOut {
  return {
    entry_id: "entry-1",
    service: "spotify",
    playlist_id: "pl-1",
    name: "Road trip",
    track_count: 12,
    versions: 1,
    first_backed_up_at: T0,
    last_seen_at: T0,
    missing_since: null,
    archived_at: null,
    cover_url: null,
    ...o,
  };
}

export function backupSummaryOut(o: Partial<BackupSummaryOut> = {}): BackupSummaryOut {
  return {
    services: [],
    schedule: null,
    next_backup_at: null,
    active_job: null,
    ...o,
  };
}

export function settingsOut(o: Partial<SettingsOut> = {}): SettingsOut {
  return {
    default_schedule: "0 * * * *",
    timezone: "UTC",
    brake_max_fraction: 0.5,
    brake_max_count: 50,
    match_accept_threshold: 0.9,
    match_min_margin: 0.1,
    breaker_failure_threshold: 5,
    breaker_cooldown_minutes: 30,
    backup_schedule: null,
    backup_versions_kept: 10,
    ...o,
  };
}

export function setupGuide(o: Partial<SetupGuide> = {}): SetupGuide {
  return {
    service: "spotify",
    display_name: "Spotify",
    category: "streaming",
    prerequisites: [],
    steps: [
      {
        step_id: "test",
        title: "Test the connection",
        kind: "test",
        body: "",
        copy_values: [],
        fields: [],
        links: [],
        screenshots: [],
      },
    ],
    ...o,
  };
}

export function changeOut(o: Partial<ChangeOut> = {}): ChangeOut {
  return { kind: "add", detail: "", track: null, ...o };
}

export function backupRunOut(o: Partial<BackupRunOut> = {}): BackupRunOut {
  return {
    backup_id: "bk-1",
    service: "fake_a",
    status: "succeeded",
    started_at: T0,
    finished_at: T0,
    playlists: 3,
    saved: 2,
    unchanged: 1,
    archived: 0,
    unreadable: 0,
    errors: [],
    error: null,
    ...o,
  };
}

export function backupServiceOut(o: Partial<BackupServiceOut> = {}): BackupServiceOut {
  return {
    service: "fake_a",
    display_name: "Fake A",
    connected: true,
    playlists: 3,
    archived: 1,
    missing: 0,
    last_run: null,
    ...o,
  };
}

export function statsOut(o: Partial<StatsOut> = {}): StatsOut {
  return {
    days: 30,
    since: "2025-12-02T12:00:00Z",
    library: [],
    syncs: 0,
    paused_syncs: 0,
    activity: {
      sync_runs: 0,
      transfer_runs: 0,
      undo_runs: 0,
      succeeded: 0,
      partial: 0,
      failed: 0,
      waiting: 0,
      skipped: 0,
      songs_added: 0,
      songs_removed: 0,
      backups: 0,
    },
    daily: [],
    last_backup_at: null,
    matching: { remembered: 0, not_on_service: 0, open_attention: 0 },
    ...o,
  };
}
