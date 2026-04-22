ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'project';
CREATE INDEX idx_jobs_job_type ON jobs(job_type);
