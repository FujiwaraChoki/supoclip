-- Ask before making clips from a video with little or no speech (the refunded
-- low_speech stop). On for web; off for iOS until the app build that explains it
-- ships (older builds only show a generic failure). Admins bypass to test it.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'low_speech_notice',
    'Stop a video with little or no speech after transcription, refund it, and offer "Create clips anyway". Read when a task is created, for the platform that creates it. Turn iOS on once the app build with the notice is live.',
    true,
    false,
    true
)
ON CONFLICT (key) DO NOTHING;
