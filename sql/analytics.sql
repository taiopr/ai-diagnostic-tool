-- analytics.sql
-- Analytical queries for the AI Diagnostic Tool database.
-- Each query answers a specific client-facing question and was verified
-- against real session/issue data (not toy data) before being finalized here.

-- 1. What's the most common failure pattern across all diagnostics run?
-- Answers: "Which prompt-quality issue should we prioritize fixing first?"
SELECT type, COUNT(*) as occurrences,
       ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM sessions), 1) as pct_of_sessions
FROM issues
GROUP BY type
ORDER BY occurrences DESC;


-- 2. Does failure rate vary by hour of day?
-- Answers: "Is there a time-based pattern in poor-quality submissions?"
-- Note: created_at is stored without timezone info but represents UTC wall-clock
-- time (confirmed via a uniform +2 hour shift after correction). Converted to
-- Europe/Madrid local time here so "hour of day" reflects when sessions actually
-- happened for a Barcelona-based user, not raw UTC. With a personal/low-volume
-- dataset, this still mostly reflects what kind of prompts were being tested
-- during a given hour rather than a genuine time-of-day effect. Percentages
-- from hours with very few sessions should be treated as unreliable noise.
SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Madrid') as hour_of_day,
       COUNT(*) as total_sessions,
       COUNT(*) FILTER (WHERE score < 40) as low_score_sessions,
       ROUND(COUNT(*) FILTER (WHERE score < 40) * 100.0 / COUNT(*), 1) as low_score_pct
FROM sessions
GROUP BY hour_of_day
ORDER BY hour_of_day;


-- 3. How does response time compare between prompt mode and n8n workflow mode?
-- Answers: "Is one input type significantly slower to process than the other?"
-- Finding: averages are close (~7% apart), consistent with response time
-- being driven by token count and the fixed two-call Claude pipeline rather
-- than by input_mode itself. n8n_workflow has a much smaller sample size,
-- so its numbers carry proportionally less confidence than prompt mode's.
SELECT input_mode,
       ROUND(AVG(response_time_ms)::numeric, 0) as avg_response_time_ms,
       ROUND(MIN(response_time_ms)::numeric, 0) as fastest_ms,
       ROUND(MAX(response_time_ms)::numeric, 0) as slowest_ms,
       COUNT(*) as sample_size
FROM sessions
WHERE response_time_ms IS NOT NULL
GROUP BY input_mode;


-- 4. Is average prompt quality trending up or down over time?
-- Answers: "Is usage of the tool improving over time?"
-- Finding: the daily average reflects which mix of prompts was tested that
-- day (deliberately bad debugging prompts vs. well-structured demo prompts),
-- not any real improvement in the tool itself. A client-facing version of
-- this report would need this caveat explicitly stated, not presented as a
-- literal performance trend line.
SELECT DATE(created_at) as day,
       COUNT(*) as sessions_run,
       ROUND(AVG(score)::numeric, 1) as avg_score
FROM sessions
GROUP BY DATE(created_at)
ORDER BY day;


-- 5. Does issue severity actually correlate with overall prompt quality?
-- Answers: "Can the severity label be trusted as a real signal of how bad
-- a prompt is, or is it arbitrary?"
-- Finding: confirmed — high severity issues correlate with the lowest
-- average session scores (20.9), medium in between (29.8), low the highest
-- (42.4). Clean monotonic relationship, validating severity as meaningful.
-- Note: a session can have issues of multiple severities simultaneously, so
-- these three groups are not mutually exclusive sets of sessions. Also,
-- "low" severity has a much smaller issue count than high/medium, so that
-- average carries less statistical weight than the other two.
SELECT i.severity,
       COUNT(*) as issue_count,
       ROUND(AVG(s.score)::numeric, 1) as avg_session_score
FROM issues i
JOIN sessions s ON i.session_id = s.id
GROUP BY i.severity
ORDER BY 
    CASE i.severity 
        WHEN 'high' THEN 1 
        WHEN 'medium' THEN 2 
        WHEN 'low' THEN 3 
    END;