"""
Multi-LLM council deliberation and voting system.
"""
import logging
import json
from typing import List, Dict, Any
from .models import (
    CouncilMember,
    COUNCIL_DISPLAY_NAMES,
    ClipCandidate,
    ModelAnalysis,
    CouncilDeliberation,
    CouncilVote
)
from ..utils.openrouter_client import call_openrouter

logger = logging.getLogger(__name__)


def calculate_target_clips(video_duration_seconds: float) -> int:
    """
    Calculate target number of clips based on video duration.

    Rules:
    - 0-15 min (0-900s): 50 clips
    - 15-90 min (900-5400s): 250 clips
    - 90+ min (5400s+): 500 clips

    Args:
        video_duration_seconds: Video duration in seconds

    Returns:
        Target number of clips
    """
    if video_duration_seconds <= 900:  # 15 minutes
        return 50
    elif video_duration_seconds <= 5400:  # 90 minutes
        return 250
    else:
        return 500


async def run_council_analysis(
    transcript: str,
    video_duration: float,
    user_notes: str = ""
) -> CouncilDeliberation:
    """
    Run 5-model council analysis with deliberation.

    Phase 1: Each model independently analyzes transcript
    Phase 2: Models vote on each other's candidates
    Phase 3: Select final clips based on votes

    Args:
        transcript: Full video transcript with timestamps
        video_duration: Video duration in seconds
        user_notes: User instructions for what to look for

    Returns:
        CouncilDeliberation with final selected clips
    """
    target_clips = calculate_target_clips(video_duration)
    logger.info(f"🎯 Video duration: {video_duration/60:.1f} min → Target clips: {target_clips}")

    # PHASE 1: Independent analysis by each model
    logger.info("📊 Phase 1: Independent analysis by 5 council members")
    model_analyses = await phase_1_independent_analysis(
        transcript,
        video_duration,
        target_clips,
        user_notes
    )

    # PHASE 2: Collect all candidates and run voting
    logger.info("🗳️  Phase 2: Council voting on candidates")
    all_candidates = []
    for analysis in model_analyses:
        all_candidates.extend(analysis.candidates)

    logger.info(f"Total candidates from all models: {len(all_candidates)}")

    # Remove duplicates (clips within 5 seconds of each other)
    deduplicated = deduplicate_candidates(all_candidates)
    logger.info(f"After deduplication: {len(deduplicated)} candidates")

    # Vote on each candidate
    voted_candidates = await phase_2_voting(
        deduplicated,
        model_analyses,
        transcript,
        user_notes
    )

    # PHASE 3: Select top clips based on votes
    logger.info("✅ Phase 3: Selecting final clips")
    final_candidates = select_final_clips(voted_candidates, target_clips)

    # Calculate consensus
    consensus = calculate_consensus(voted_candidates, final_candidates)

    return CouncilDeliberation(
        video_duration=video_duration,
        target_clips=target_clips,
        model_analyses=model_analyses,
        final_candidates=final_candidates,
        consensus_level=consensus
    )


async def phase_1_independent_analysis(
    transcript: str,
    video_duration: float,
    target_clips: int,
    user_notes: str
) -> List[ModelAnalysis]:
    """
    Phase 1: Each model independently analyzes transcript.

    Args:
        transcript: Full transcript
        video_duration: Duration in seconds
        target_clips: Target number of clips
        user_notes: User instructions

    Returns:
        List of model analyses
    """
    system_prompt = f"""You are a video content analyst in a 5-member AI council.

Your task: Analyze this video transcript and identify the most interesting/engaging moments for short clips.

VIDEO CONTEXT:
- Duration: {video_duration/60:.1f} minutes
- Target clips: {target_clips}

USER INSTRUCTIONS:
{user_notes or "No specific instructions - use your best judgment for engaging content."}

ANALYSIS CRITERIA:
- Look for peaks in action, emotion, or storytelling
- Identify quotable moments, surprising content, or strong hooks
- Consider variety (different types of moments across the video)
- Each clip should be 10-60 seconds long
- Prioritize moments that could standalone without full context

CRITICAL: Respond with valid JSON only:
{{
  "candidates": [
    {{
      "start_time": "MM:SS",
      "end_time": "MM:SS",
      "duration": <seconds>,
      "title": "Brief descriptive title",
      "reasoning": "Why this moment is interesting",
      "engagement_score": <0-10>,
      "category": "action|story|emotional|quotable|reaction"
    }}
  ],
  "overall_assessment": "Your high-level thoughts on the content",
  "recommended_total_clips": <your suggested clip count>
}}

Identify approximately {target_clips // 5} to {target_clips // 3} candidates."""

    # Call all 5 models in parallel
    import asyncio

    tasks = []
    for member in CouncilMember:
        task = call_openrouter(
            model=member.value,
            prompt=transcript,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=4000,
            json_mode=True
        )
        tasks.append((member, task))

    results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)

    # Parse results
    analyses = []
    for i, (member, _) in enumerate(tasks):
        result = results[i]

        if isinstance(result, Exception):
            logger.error(f"❌ {COUNCIL_DISPLAY_NAMES[member]} failed: {result}")
            continue

        try:
            data = json.loads(result)

            # Parse candidates
            candidates = []
            for c in data.get('candidates', []):
                # Validate timestamps
                start_parts = c['start_time'].split(':')
                end_parts = c['end_time'].split(':')

                start_seconds = int(start_parts[0]) * 60 + int(start_parts[1])
                end_seconds = int(end_parts[0]) * 60 + int(end_parts[1])

                if end_seconds <= start_seconds:
                    logger.warning(f"Invalid timestamps: {c['start_time']} -> {c['end_time']}")
                    continue

                candidates.append(ClipCandidate(
                    start_time=c['start_time'],
                    end_time=c['end_time'],
                    duration=c.get('duration', end_seconds - start_seconds),
                    title=c['title'],
                    reasoning=c['reasoning'],
                    engagement_score=c['engagement_score'],
                    category=c['category']
                ))

            analysis = ModelAnalysis(
                model_name=COUNCIL_DISPLAY_NAMES[member],
                candidates=candidates,
                overall_assessment=data.get('overall_assessment', ''),
                recommended_total_clips=data.get('recommended_total_clips', target_clips)
            )

            analyses.append(analysis)
            logger.info(f"✅ {analysis.model_name}: {len(candidates)} candidates")

        except Exception as e:
            logger.error(f"❌ Failed to parse {COUNCIL_DISPLAY_NAMES[member]} response: {e}")
            logger.error(f"Response: {result[:500]}...")

    return analyses


async def phase_2_voting(
    candidates: List[ClipCandidate],
    model_analyses: List[ModelAnalysis],
    transcript: str,
    user_notes: str
) -> List[Dict[str, Any]]:
    """
    Phase 2: Models vote on all candidates.

    Args:
        candidates: All clip candidates
        model_analyses: Previous analyses
        transcript: Full transcript
        user_notes: User instructions

    Returns:
        Candidates with vote counts
    """
    # For efficiency, only vote on top candidates from phase 1
    # Sort by engagement score and take top 2x target
    sorted_candidates = sorted(candidates, key=lambda c: c.engagement_score, reverse=True)
    candidates_to_vote = sorted_candidates[:len(candidates) // 2]  # Top half

    logger.info(f"Voting on top {len(candidates_to_vote)} candidates")

    # Prepare voting prompt
    candidates_json = json.dumps([
        {
            "index": i,
            "start_time": c.start_time,
            "end_time": c.end_time,
            "title": c.title,
            "reasoning": c.reasoning,
            "score": c.engagement_score
        }
        for i, c in enumerate(candidates_to_vote)
    ], indent=2)

    voting_system_prompt = f"""You are voting on clip candidates proposed by the council.

USER INSTRUCTIONS:
{user_notes or "No specific instructions."}

Vote YES or NO on each candidate based on:
1. Does it match user instructions?
2. Is it genuinely interesting/engaging?
3. Would it work as a standalone clip?

Respond with JSON:
{{
  "votes": [
    {{
      "clip_index": <index>,
      "vote": "YES" or "NO",
      "confidence": <0-1>,
      "reasoning": "Brief explanation"
    }}
  ]
}}"""

    # Each model votes on all candidates
    import asyncio

    vote_tasks = []
    for member in CouncilMember:
        task = call_openrouter(
            model=member.value,
            prompt=f"Vote on these clip candidates:\n\n{candidates_json}",
            system_prompt=voting_system_prompt,
            temperature=0.5,
            max_tokens=3000,
            json_mode=True
        )
        vote_tasks.append((member, task))

    vote_results = await asyncio.gather(*[task for _, task in vote_tasks], return_exceptions=True)

    # Aggregate votes
    vote_counts = [{"candidate": c, "yes_votes": 0, "no_votes": 0, "total_confidence": 0.0}
                   for c in candidates_to_vote]

    for i, (member, _) in enumerate(vote_tasks):
        result = vote_results[i]

        if isinstance(result, Exception):
            logger.error(f"❌ {COUNCIL_DISPLAY_NAMES[member]} voting failed: {result}")
            continue

        try:
            data = json.loads(result)
            for vote in data.get('votes', []):
                idx = vote['clip_index']
                if idx < len(vote_counts):
                    if vote['vote'] == 'YES':
                        vote_counts[idx]['yes_votes'] += 1
                        vote_counts[idx]['total_confidence'] += vote.get('confidence', 1.0)
                    else:
                        vote_counts[idx]['no_votes'] += 1

            logger.info(f"✅ {COUNCIL_DISPLAY_NAMES[member]}: Voted on {len(data.get('votes', []))} candidates")

        except Exception as e:
            logger.error(f"❌ Failed to parse votes from {COUNCIL_DISPLAY_NAMES[member]}: {e}")

    return vote_counts


def deduplicate_candidates(candidates: List[ClipCandidate]) -> List[ClipCandidate]:
    """
    Remove duplicate candidates (clips within 5 seconds of each other).

    Args:
        candidates: All candidates

    Returns:
        Deduplicated list
    """
    if not candidates:
        return []

    # Sort by start time
    sorted_candidates = sorted(
        candidates,
        key=lambda c: int(c.start_time.split(':')[0]) * 60 + int(c.start_time.split(':')[1])
    )

    deduplicated = [sorted_candidates[0]]

    for candidate in sorted_candidates[1:]:
        # Get last added candidate
        last = deduplicated[-1]

        # Parse timestamps
        last_start = int(last.start_time.split(':')[0]) * 60 + int(last.start_time.split(':')[1])
        curr_start = int(candidate.start_time.split(':')[0]) * 60 + int(candidate.start_time.split(':')[1])

        # If more than 5 seconds apart, it's a new clip
        if curr_start - last_start > 5:
            deduplicated.append(candidate)
        else:
            # Keep the one with higher engagement score
            if candidate.engagement_score > last.engagement_score:
                deduplicated[-1] = candidate

    return deduplicated


def select_final_clips(
    voted_candidates: List[Dict[str, Any]],
    target_clips: int
) -> List[ClipCandidate]:
    """
    Select final clips based on votes.

    Args:
        voted_candidates: Candidates with vote counts
        target_clips: Target number of clips

    Returns:
        Final selected clips
    """
    # Sort by yes votes, then by total confidence
    sorted_candidates = sorted(
        voted_candidates,
        key=lambda x: (x['yes_votes'], x['total_confidence']),
        reverse=True
    )

    # Take top N
    final = [item['candidate'] for item in sorted_candidates[:target_clips]]

    logger.info(f"✅ Selected {len(final)} final clips")

    # Log vote distribution
    if final:
        avg_yes_votes = sum(item['yes_votes'] for item in sorted_candidates[:target_clips]) / len(final)
        logger.info(f"Average YES votes for selected clips: {avg_yes_votes:.1f} / 5")

    return final


def calculate_consensus(
    voted_candidates: List[Dict[str, Any]],
    final_candidates: List[ClipCandidate]
) -> float:
    """
    Calculate consensus level (0-1) based on voting agreement.

    Args:
        voted_candidates: All voted candidates
        final_candidates: Final selected clips

    Returns:
        Consensus score 0-1
    """
    if not voted_candidates or not final_candidates:
        return 0.0

    # Find the final candidates in voted list
    final_votes = [
        item for item in voted_candidates
        if item['candidate'] in final_candidates
    ]

    if not final_votes:
        return 0.0

    # Calculate average yes vote percentage
    total_yes_pct = sum(
        item['yes_votes'] / 5.0  # 5 models
        for item in final_votes
    ) / len(final_votes)

    return total_yes_pct
