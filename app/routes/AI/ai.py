from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import User, WritingTask, WritingAnswer
from app.routes.admin.auth import get_current_student
from typing import Dict
from bs4 import BeautifulSoup
from datetime import datetime
from app.utils.datetime_utils import get_vietnam_time
import groq
import os
import json  # Add this import
from pydantic import BaseModel

router = APIRouter()

# Create two separate clients with different API keys
client_evaluation = groq.Groq(api_key=os.getenv("GROQ_API_KEY"))
client_rewriting = groq.Groq(api_key=os.getenv("GROQ_REWRITING_API_KEY"))

class EssayEvaluationRequest(BaseModel):
    part_number: int
    essay_text: str
    instructions: str
    task_id: int

async def evaluate_with_groq(essay_text: str, instructions: str, part_number: int) -> Dict:
    # Clean up the input text
    essay_text = essay_text.strip().replace('\n', ' ').replace('\r', '')
    instructions = instructions.strip().replace('\n', ' ').replace('\r', '')
    
    task_type = "Task 1" if part_number == 1 else "Task 2"
    word_count = len(essay_text.split())
    

    target_words_task1 = 350  # Increased for Band 8+
    target_words_task2 = 650  # Increased for Band 8+
    
    # Evaluation prompt (without rewriting part)
    evaluation_prompt = f'''You are an expert IELTS Writing Examiner with 15+ years of experience. Evaluate this IELTS Writing {task_type} essay using the official IELTS band descriptors (0-9 scale). Be thorough and identify ALL mistakes present in the essay.

**CRITICAL EVALUATION REQUIREMENTS:**
1. **COMPREHENSIVE MISTAKE IDENTIFICATION:**
   - Examine EVERY sentence for errors in grammar, vocabulary, task response, and coherence
   - Identify ALL instances of:
     * Grammatical errors (tense, subject-verb agreement, articles, prepositions, sentence structure)
     * Vocabulary mistakes (word choice, collocation, register, spelling)
     * Task achievement issues (incomplete answers, irrelevant content, insufficient development)
     * Coherence problems (unclear connections, poor paragraph structure, weak transitions)
   - For each criterion, find AT LEAST 5-8 specific mistakes if they exist
   - Do not limit yourself to 2-3 mistakes per criterion - find ALL errors present

2. **ACCURATE BAND SCORING:**

### **IELTS Writing Task 1: Core Assessment Checklist**

**(Target: 250+ words | Assess based on performance)**

#### **1. Task Achievement (TA)**

* **Overview:** Is there a clear, accurate summary of the main trends, differences, or stages in the introduction or as a clear opening/closing paragraph? (Absence of a clear overview caps the score at Band 5).
* **Key Features:** Are the most significant and obvious points from the visual data selected and reported? Or for General Training (GT), are all three bullet points fully and appropriately addressed?
* **Data/Details:** Is the supporting data accurate? Is there a good balance between key points and supporting details, or is it just a list of data?
* **Tone (GT only):** Is the tone (formal/semi-formal/informal) consistently and appropriately maintained throughout the letter?

#### **2. Coherence and Cohesion (CC)**

* **Paragraphing:** Is the information logically grouped into paragraphs? (e.g., Introduction/Overview, Body Paragraph 1 for key feature A, Body Paragraph 2 for key feature B).
* **Progression:** Is there a clear and logical flow of information from start to finish?
* **Linking:** Are cohesive devices (e.g., "In contrast," "Furthermore," "This shows...") used accurately and effectively, without being mechanical or causing confusion?

#### **3. Lexical Resource (LR)**

* **Range & Precision:** Does the candidate use a varied and precise vocabulary to describe trends, comparisons, or make requests? (e.g., "rose sharply," "plateaued," "fluctuated" vs. just "went up/down").
* **Less Common Vocabulary:** Are there skillful attempts at using less common words and collocations (e.g., "a corresponding decline," "remained relatively static")?
* **Errors:** What is the impact of errors in word choice, spelling, or word formation? Do they impede understanding? (Minor slips are acceptable at high bands).

#### **4. Grammatical Range and Accuracy (GRA)**

* **Sentence Variety:** Is there a mix of simple, compound, and complex sentence structures? (A script with only simple sentences is capped at Band 5).
* **Error-Free Sentences:** What percentage of sentences are completely free from grammatical errors? (Band 7+ requires frequent error-free sentences).
* **Error Impact:** What is the nature and frequency of grammatical errors (e.g., tense, prepositions, articles)? Do they cause confusion for the reader?

---

### **IELTS Writing Task 2: Core Assessment Checklist**

**(Target: 450+ words | Double Weight | Assess based on performance)**

#### **1. Task Response (TR)**

* **Address the Entire Question:** Has every part of the prompt been fully addressed? (e.g., For "Discuss both views and give your own opinion," are both views discussed *and* is a clear opinion present?). Partial address limits the score to Band 5.
* **Clear Position:** Is the author's opinion or stance clear and consistent from the introduction to the conclusion?
* **Idea Development:** Are the main ideas supported with relevant explanations and specific examples? Is there a tendency to overgeneralize, or are points well-developed and extended?
* **Relevance:** Do all arguments directly relate to the question asked, or does the essay drift off-topic?

#### **2. Coherence and Cohesion (CC)**

* **Logical Structure:** Is there a clear and effective essay structure (Intro, Body Paragraphs, Conclusion)?
* **Paragraph Focus:** Does each body paragraph have a clear central idea that contributes to the overall argument?
* **Cohesive Flow:** Are ideas linked effectively across the essay? Is referencing (e.g., "this issue," "these solutions") clear and accurate?

#### **3. Lexical Resource (LR)**

* **Topic-Specific Vocabulary:** Does the candidate demonstrate a broad vocabulary relevant to the essay's topic?
* **Precision & Style:** Is vocabulary used with precision and an awareness of style and collocation (e.g., not just "good/bad" but "beneficial/detrimental," "a significant drawback")?
* **Error Impact:** How do vocabulary errors affect communication and precision?

#### **4. Grammatical Range and Accuracy (GRA)**

* **Structural Variety:** Does the candidate demonstrate flexible and accurate use of complex structures (e.g., conditionals, relative clauses, passive voice) to build their argument?
* **Control & Accuracy:** What is the balance between simple and complex sentences, and what is the degree of accuracy in each?
* **Error Impact:** Do grammatical and punctuation errors strain the reader or obscure the argument? (Frequent errors that cause difficulty will result in Band 5 or lower).

### **Final Scoring Protocol**

1.  **Score Each Criterion (1-9):** Assign a definitive band score for each of the four criteria for both tasks.
2.  **Calculate Task Scores:** Average the four scores for each task (e.g., Task 2: TR 7, CC 7, LR 6, GRA 6 = 26/4 = 6.5).
3.  **Calculate Final Weighted Score:** Apply the 2:1 weighting: **(Task 1 Score + (Task 2 Score x 2)) / 3**. Round to the nearest half-band (e.g., a 6.75 becomes 7.0, a 6.25 becomes 6.5).

3. **TASK-SPECIFIC REQUIREMENTS:**
   **Task 1 (Academic):** Must describe visual data accurately, highlight key features, make comparisons, use appropriate academic language
   **Task 2:** Must address all parts of the question, present clear position, develop arguments with examples, maintain formal academic tone

4. **VIETNAMESE TRANSLATION REQUIREMENTS:**
   - ALL mistake explanations MUST be written in accurate, natural Vietnamese
   - ALL improvement suggestions MUST be written in fluent, precise Vietnamese
   - Use proper Vietnamese grammar, vocabulary, and sentence structure
   - Avoid direct translations - use natural Vietnamese expressions
   - Use formal, educational tone appropriate for language learning

**JSON OUTPUT REQUIREMENTS:**
- Your response MUST be valid JSON format
- Use double quotes for all strings
- Escape all special characters properly (\\n for newlines, \" for quotes)
- Do not include any text outside the JSON structure

**EVALUATION PROCESS:**
1. Read the entire essay carefully
2. Check word count and apply penalties if necessary
3. Systematically examine each sentence for all types of errors
4. Score each criterion based on the weakest aspect within that criterion
5. Calculate overall band score (not an average - consider the weakest criterion)
6. Provide comprehensive feedback with ALL identified mistakes
7. Provide clear and concise improvement suggestions for ALL mistakes
8. Rewrite the mistake phrase with the corrected version
Return your evaluation in the exact JSON format below. All explanations and suggestions MUST be in accurate, natural Vietnamese.

{{
    "band_score": "<overall_score>",
    "criteria_scores": {{
        "task_achievement": "<score>",
        "coherence_cohesion": "<score>",
        "lexical_resource": "<score>",
        "grammatical_range": "<score>"
    }},
    "mistakes": {{
        "task_achievement": [
            {{"phrase": "<original_text>", "explanation": "<loi_bang_tieng_viet>"}}
        ],
        "coherence_cohesion": [
            {{"phrase": "<original_text>", "explanation": "<loi_bang_tieng_viet>"}}
        ],
        "lexical_resource": [
            {{"phrase": "<original_text>", "explanation": "<loi_bang_tieng_viet>"}}
        ],
        "grammatical_range": [
            {{"phrase": "<original_text>", "explanation": "<loi_bang_tieng_viet>"}}
        ]
    }},
    "improvement_suggestions": {{
        "task_achievement": [
            {{"phrase": "<original_text>", "suggestion": "<rewrite_correct_version_of_orignial_text>"}}
        ],
        "coherence_cohesion": [
            {{"phrase": "<original_text>", "suggestion": "<rewrite_correct_version_of_orignial_text>"}}
        ],
        "lexical_resource": [
            {{"phrase": "<original_text>", "suggestion": "<rewrite_correct_version_of_orignial_text>"}}
        ],
        "grammatical_range": [
            {{"phrase": "<original_text>", "suggestion": "<rewrite_correct_version_of_orignial_text>"}}
        ]
    }}
}}

**Task Type:** IELTS Writing {task_type}
**Task Instructions:** {instructions}
**Student Essay ({word_count} words):** {essay_text}

**IMPORTANT:** Your response must be ONLY valid JSON. Do not include any explanatory text before or after the JSON. Ensure all strings are properly escaped.'''

    # Rewriting prompt
    rewriting_prompt = f'''You are an expert IELTS Writing Examiner with 15+ years of experience. Your task is to rewrite the following IELTS Writing {task_type} essay to demonstrate Band 8.0+ standard.

**REWRITTEN ESSAY STANDARDS (CRITICAL FOR BAND 8+):**
   - Target word count: 
     * Task 1: Must be longer than {target_words_task1} words (Band 8+ standard)
     * Task 2: Must be longer than {target_words_task2} words (Band 8+ standard)
   - Band 8.0+ level: Wide range of vocabulary, natural and sophisticated language, flexible sentence structures, clear progression
   - MUST include proper paragraph formatting with double newline characters (\\n\\n) between paragraphs
   - Essay structure with clear paragraph breaks:
     * Task 1: Introduction\\n\\ Overview\\n\\nBody paragraph 1\\n\\nBody paragraph 2 (if needed)
     * Task 2: Introduction\\n\\nBody paragraph 1\\n\\nBody paragraph 2\\n\\nConclusion
   - Use sophisticated linking words and cohesive devices
   - Demonstrate lexical variety and grammatical complexity

**JSON OUTPUT REQUIREMENTS:**
- Your response MUST be valid JSON format
- Use double quotes for all strings
- Escape all special characters properly (\\n for newlines, \" for quotes)
- Do not include any text outside the JSON structure
- Ensure the rewritten_essay field contains proper \\n\\n between paragraphs

**Task Type:** IELTS Writing {task_type}
**Task Instructions:** {instructions}
**Student Essay ({word_count} words):** {essay_text}

**IMPORTANT:** Your response must be ONLY valid JSON with the following structure:
{{
    "rewritten_essay": "<essay_with_proper_paragraph_breaks_using_newlines>"
}}

Do not include any explanatory text before or after the JSON. Ensure all strings are properly escaped and the rewritten essay includes \\n\\n between paragraphs.'''

    try:
        # First model for evaluation (llama3-70b-8192)
        evaluation_completion = client_evaluation.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Using llama3 for evaluation
            messages=[
                {"role": "system", "content": "You are an expert IELTS examiner with 15+ years of experience. You must identify ALL mistakes in student essays and provide accurate band scores according to official IELTS criteria. Always respond in the exact JSON format specified."},
                {"role": "user", "content": evaluation_prompt}
            ],
            temperature=0.6,
            max_tokens=5000
        )
        
        # Second model for essay rewriting (claude-3-opus-20240229)
        rewriting_completion = client_rewriting.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Using Claude for rewriting
            messages=[
                {"role": "system", "content": "You are an expert IELTS examiner with 15+ years of experience. Your task is to rewrite student essays to demonstrate Band 8.0+ standard. Always respond in the exact JSON format specified."},
                {"role": "user", "content": rewriting_prompt}
            ],
            temperature=1,  # Slightly higher temperature for more creative rewriting
            max_tokens=6000
        )
        
        # Parse both responses
        evaluation_result = parse_evaluation_response(evaluation_completion.choices[0].message.content)
        rewriting_result = parse_rewriting_response(rewriting_completion.choices[0].message.content)
        
        # Combine the results
        evaluation_result["rewritten_essay"] = rewriting_result["rewritten_essay"]
        
        return evaluation_result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI evaluation failed: {str(e)}"
        )

def parse_evaluation_response(response: str) -> Dict:
    try:
        evaluation = json.loads(response)
        
        # Initialize default structures for missing fields
        default_criteria = {
            "task_achievement": [],
            "coherence_cohesion": [],
            "lexical_resource": [],
            "grammatical_range": []
        }
        
        # Ensure all required sections exist
        evaluation['mistakes'] = evaluation.get('mistakes', default_criteria.copy())
        evaluation['improvement_suggestions'] = evaluation.get('improvement_suggestions', default_criteria.copy())
        
        # Ensure all criteria exist in mistakes and suggestions
        for category in ['task_achievement', 'coherence_cohesion', 'lexical_resource', 'grammatical_range']:
            evaluation['mistakes'][category] = evaluation['mistakes'].get(category, [])
            evaluation['improvement_suggestions'][category] = evaluation['improvement_suggestions'].get(category, [])
        
        return {
            "band_score": float(evaluation.get('band_score', 0.0)),
            "criteria_scores": {
                "task_achievement": float(evaluation.get('criteria_scores', {}).get('task_achievement', 0.0)),
                "coherence_cohesion": float(evaluation.get('criteria_scores', {}).get('coherence_cohesion', 0.0)),
                "lexical_resource": float(evaluation.get('criteria_scores', {}).get('lexical_resource', 0.0)),
                "grammatical_range": float(evaluation.get('criteria_scores', {}).get('grammatical_range', 0.0))
            },
            "mistakes": evaluation['mistakes'],
            "improvement_suggestions": evaluation['improvement_suggestions'],
            "rewritten_essay": ""  # This will be filled by the rewriting model
        }
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse AI evaluation response as JSON: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process AI evaluation response: {str(e)}"
        )

def parse_rewriting_response(response: str) -> Dict:
    try:
        rewriting = json.loads(response)
        
        return {
            "rewritten_essay": rewriting.get('rewritten_essay', '')
        }
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse AI rewriting response as JSON: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process AI rewriting response: {str(e)}"
        )

@router.post("/evaluate", response_model=Dict)
async def evaluate_essay(
    request: EssayEvaluationRequest = Body(..., embed=True),
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Clean HTML tags from input data
    essay_text = BeautifulSoup(request.essay_text, "html.parser").get_text().strip()
    instructions = BeautifulSoup(request.instructions, "html.parser").get_text().strip()
    
    # Validate input
    if not essay_text or not instructions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Essay text and instructions cannot be empty"
        )
    
    task = db.query(WritingTask).filter(
        WritingTask.task_id == request.task_id
    ).first()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Writing task not found"
        )

    evaluation = await evaluate_with_groq(
        essay_text=essay_text,
        instructions=instructions
    )

    return {
        "task_id": request.task_id,
        "evaluation_timestamp": get_vietnam_time().replace(tzinfo=None),
        "word_count": len(essay_text.split()),
        "evaluation_result": evaluation
    }

@router.post("/evaluate-and-save/{task_id}", response_model=Dict)
async def evaluate_and_save_essay(
    task_id: int,
    essay_text: str = Body(...),
    instructions: str = Body(...),
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    task = db.query(WritingTask).filter(
        WritingTask.task_id == task_id
    ).first()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Writing task not found"
        )

    # Clean HTML tags from input data
    clean_essay = BeautifulSoup(essay_text, "html.parser").get_text().strip()
    clean_instructions = BeautifulSoup(instructions, "html.parser").get_text().strip()

    # Check if essay already has AI evaluation
    existing_answer = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id,
        WritingAnswer.user_id == current_student.user_id,
        WritingAnswer.is_ai_evaluated == True
    ).first()

    if existing_answer:
        return {
            "task_id": task_id,
            "evaluation_timestamp": existing_answer.updated_at,
            "word_count": len(existing_answer.answer_text.split()),
            "evaluation_result": {
                "band_score": existing_answer.score,
                "criteria_scores": {
                    "task_achievement": existing_answer.task_achievement_score,
                    "coherence_cohesion": existing_answer.coherence_cohesion_score,
                    "lexical_resource": existing_answer.lexical_resource_score,
                    "grammatical_range": existing_answer.grammatical_range_score
                },
                "mistakes": existing_answer.mistakes,
                "improvement_suggestions": existing_answer.improvement_suggestions,
                "rewritten_essay": existing_answer.rewritten_essay
            },
            "saved": True,
            "answer_id": existing_answer.answer_id,
            "is_ai_evaluated": True
        }

    evaluation = await evaluate_with_groq(
        essay_text=clean_essay,
        instructions=clean_instructions,
        part_number=task.part_number
    )

    writing_answer = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id,
        WritingAnswer.user_id == current_student.user_id
    ).first()

    if writing_answer:
        writing_answer.answer_text = essay_text
        writing_answer.score = evaluation["band_score"]
        writing_answer.task_achievement_score = evaluation["criteria_scores"]["task_achievement"]
        writing_answer.coherence_cohesion_score = evaluation["criteria_scores"]["coherence_cohesion"]
        writing_answer.lexical_resource_score = evaluation["criteria_scores"]["lexical_resource"]
        writing_answer.grammatical_range_score = evaluation["criteria_scores"]["grammatical_range"]
        writing_answer.mistakes = evaluation["mistakes"]
        writing_answer.improvement_suggestions = evaluation["improvement_suggestions"]
        writing_answer.rewritten_essay = evaluation["rewritten_essay"]
        writing_answer.is_ai_evaluated = True
        writing_answer.updated_at = get_vietnam_time().replace(tzinfo=None)
    else:
        writing_answer = WritingAnswer(
            task_id=task_id,
            user_id=current_student.user_id,
            answer_text=essay_text,
            score=evaluation["band_score"],
            task_achievement_score=evaluation["criteria_scores"]["task_achievement"],
            coherence_cohesion_score=evaluation["criteria_scores"]["coherence_cohesion"],
            lexical_resource_score=evaluation["criteria_scores"]["lexical_resource"],
            grammatical_range_score=evaluation["criteria_scores"]["grammatical_range"],
            mistakes=evaluation["mistakes"],
            improvement_suggestions=evaluation["improvement_suggestions"],
            rewritten_essay=evaluation["rewritten_essay"],
            created_at=get_vietnam_time().replace(tzinfo=None),
            updated_at=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(writing_answer)

    db.commit()
    db.refresh(writing_answer)

    return {
        "task_id": task_id,
        "evaluation_timestamp": get_vietnam_time().replace(tzinfo=None),
        "word_count": len(clean_essay.split()),
        "evaluation_result": evaluation,
        "saved": True,
        "answer_id": writing_answer.answer_id
    }

@router.get("/evaluation/{answer_id}", response_model=Dict)
async def get_evaluation_data(
    answer_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    writing_answer = db.query(WritingAnswer).filter(
        WritingAnswer.answer_id == answer_id,
        WritingAnswer.user_id == current_student.user_id
    ).first()
    
    if not writing_answer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Writing answer not found"
        )

    return {
        "task_id": writing_answer.task_id,
        "evaluation_timestamp": writing_answer.updated_at,
        "word_count": len(writing_answer.answer_text.split()),
        "answer_text": writing_answer.answer_text,
        "evaluation_result": {
            "band_score": writing_answer.score,
            "criteria_scores": {
                "task_achievement": writing_answer.task_achievement_score,
                "coherence_cohesion": writing_answer.coherence_cohesion_score,
                "lexical_resource": writing_answer.lexical_resource_score,
                "grammatical_range": writing_answer.grammatical_range_score
            },
            "mistakes": writing_answer.mistakes,
            "improvement_suggestions": writing_answer.improvement_suggestions,
            "rewritten_essay": writing_answer.rewritten_essay
        }
    }
@router.get("/evaluation-status/{answer_id}", response_model=Dict)
async def get_evaluation_status(
    answer_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    writing_answer = db.query(WritingAnswer).filter(
        WritingAnswer.answer_id == answer_id,
        WritingAnswer.user_id == current_student.user_id
    ).first()
    
    if not writing_answer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Writing answer not found"
        )

    return {
        "is_ai_evaluated": writing_answer.is_ai_evaluated,
        "answer_id": writing_answer.answer_id,
        "task_id": writing_answer.task_id
    }
