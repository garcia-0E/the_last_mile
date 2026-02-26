# Knowledge Base Pipeline Documentation

## Overview

The Knowledge Base Pipeline is an Apache Airflow DAG that processes documents into an AI-powered knowledge base. The pipeline extracts documents, tokenizes text, creates overlapping chunks, analyzes content using AI models, and stores the results in BigQuery.

## Architecture

```
┌─────────────────┐
│  Raw Documents  │
│   (GCS Bucket)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Extract      │
│   Documents     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Tokenize     │
│     Content     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Chunk with     │
│    Overlap      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Prepare for   │
│   AI Analysis   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  AI Model       │
│  Analysis       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Store to GCS  │
│    (JSONL)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Load to       │
│   BigQuery      │
└─────────────────┘
```

## Pipeline Steps

### 1. Extract Documents
- **Input**: Raw documents from GCS bucket (`raw-documents/` folder)
- **Process**: Downloads all files, handles encoding (UTF-8 with latin-1 fallback)
- **Output**: List of documents with metadata

### 2. Tokenize Documents
- **Input**: Document content
- **Process**: Uses tiktoken (OpenAI's tokenizer) to convert text to tokens
- **Fallback**: Word-based tokenization if tiktoken unavailable
- **Output**: Documents with token arrays and counts

### 3. Chunk Documents
- **Input**: Tokenized documents
- **Process**: 
  - Creates overlapping chunks (default: 512 tokens with 50 token overlap)
  - Maintains context between chunks through overlap
  - Decodes tokens back to text
- **Output**: Individual chunks with position metadata

### 4. Prepare for AI Analysis
- **Input**: Text chunks
- **Process**: Adds structured analysis prompts to each chunk
- **Prompt includes**:
  - Main topics/themes extraction
  - Entity recognition (people, organizations, locations)
  - Concept identification
  - Summarization
- **Output**: Chunks with analysis prompts

### 5. AI Model Analysis
- **Input**: Prepared chunks
- **Process**: Sends chunks to AI model for analysis
- **Supported Providers**:
  - OpenAI (GPT-3.5/GPT-4)
  - Google Vertex AI (Gemini)
  - Anthropic (Claude)
  - Hugging Face (Local models)
- **Output**: Chunks with extracted insights (topics, entities, concepts, summaries)

### 6. Store in Knowledge Base
- **Input**: Analyzed chunks
- **Process**: 
  - Converts to JSONL format
  - Uploads to GCS as intermediate storage
  - Loads into BigQuery table
- **Output**: Persisted knowledge base

## Configuration

### Environment Variables

```bash
# Required for GCP
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# For OpenAI (if using)
OPENAI_API_KEY=sk-...

# For Vertex AI (if using)
GCP_PROJECT_ID=your-project-id
GCP_LOCATION=us-central1

# For Anthropic (if using)
ANTHROPIC_API_KEY=sk-ant-...
```

### DAG Configuration

Edit `dags/knowledge_base_pipeline.py`:

```python
# GCP Configuration
PROJECT_NAME = "your-project-id"
DATASET_NAME = "your_dataset"
TABLE_NAME = "knowledge_base"
AIRFLOW_BUCKET = "your-bucket-name"

# Chunking Configuration
CHUNK_SIZE = 512  # tokens per chunk
CHUNK_OVERLAP = 50  # overlapping tokens
MODEL_ENCODING = "cl100k_base"  # For GPT-3.5/GPT-4
```

### AI Model Configuration

Edit `dags/knowledge_base_pipeline.py` in the `analyze_with_ai_model` task:

```python
# Import the AI analyzer
from modules.ai_analyzer import create_analyzer

# Create analyzer instance
analyzer = create_analyzer(
    provider="openai",  # or "vertex_ai", "anthropic", "huggingface"
    model="gpt-3.5-turbo"  # optional, uses defaults if not specified
)

# Use the analyzer
for chunk in prepared_chunks:
    analysis = analyzer.analyze_chunk(
        chunk['chunk_text'], 
        chunk['analysis_prompt']
    )
```

## Installation

### 1. Install Python Dependencies

Create `requirements.txt`:

```txt
# Core dependencies
apache-airflow==2.5.1
apache-airflow-providers-google==10.0.0
pandas>=1.5.0
tiktoken>=0.5.0
google-cloud-bigquery>=3.0.0
google-cloud-storage>=2.0.0

# AI Provider dependencies (install as needed)
# OpenAI
openai>=1.0.0

# Google Vertex AI
google-cloud-aiplatform>=1.34.0

# Anthropic
anthropic>=0.7.0

# Hugging Face
transformers>=4.30.0
torch>=2.0.0
```

Install:
```bash
pip install -r requirements.txt
```

### 2. Setup GCS Structure

Create the following folders in your GCS bucket:

```
your-bucket/
├── raw-documents/        # Upload source documents here
├── processed/            # Intermediate processed files
└── archive/              # Optional: for archiving processed files
```

### 3. Prepare BigQuery

The pipeline will auto-create the table, but you can create it manually:

```sql
CREATE TABLE `your-project.your-dataset.knowledge_base` (
  chunk_id STRING NOT NULL,
  document_source STRING NOT NULL,
  chunk_index INT64 NOT NULL,
  chunk_text STRING NOT NULL,
  token_count INT64 NOT NULL,
  start_position INT64 NOT NULL,
  end_position INT64 NOT NULL,
  topics STRING,
  entities STRING,
  concepts STRING,
  summary STRING,
  analyzed_at TIMESTAMP NOT NULL,
  extracted_at TIMESTAMP NOT NULL
);
```

## Usage

### 1. Upload Documents

Upload your documents to GCS:

```bash
gsutil cp document.txt gs://your-bucket/raw-documents/
gsutil cp -r documents/ gs://your-bucket/raw-documents/
```

Supported formats:
- Plain text files (.txt)
- Can be extended to support PDF, DOCX, etc.

### 2. Trigger the DAG

Via Airflow UI:
1. Navigate to http://localhost:8080
2. Find `knowledge_base_pipeline`
3. Click the play button to trigger

Via CLI:
```bash
airflow dags trigger knowledge_base_pipeline
```

### 3. Monitor Execution

Check DAG progress:
- Airflow UI: Task view shows each step
- Logs: Available for each task in the Airflow UI
- BigQuery: Query the knowledge base table

```sql
SELECT 
  document_source,
  COUNT(*) as chunk_count,
  SUM(token_count) as total_tokens
FROM `your-project.your-dataset.knowledge_base`
GROUP BY document_source
ORDER BY total_tokens DESC;
```

## Querying the Knowledge Base

### Basic Queries

**Find chunks by topic:**
```sql
SELECT chunk_text, summary, topics
FROM `your-project.your-dataset.knowledge_base`
WHERE topics LIKE '%your_topic%'
LIMIT 10;
```

**Get document summaries:**
```sql
SELECT 
  document_source,
  STRING_AGG(summary, ' | ' ORDER BY chunk_index) as full_summary
FROM `your-project.your-dataset.knowledge_base`
GROUP BY document_source;
```

**Search by entities:**
```sql
SELECT chunk_text, entities, document_source
FROM `your-project.your-dataset.knowledge_base`
WHERE entities LIKE '%company_name%'
ORDER BY chunk_index;
```

### Advanced Analytics

**Token distribution:**
```sql
SELECT 
  document_source,
  MIN(token_count) as min_tokens,
  MAX(token_count) as max_tokens,
  AVG(token_count) as avg_tokens
FROM `your-project.your-dataset.knowledge_base`
GROUP BY document_source;
```

**Recent additions:**
```sql
SELECT *
FROM `your-project.your-dataset.knowledge_base`
WHERE extracted_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
ORDER BY extracted_at DESC;
```

## Customization

### Adjusting Chunk Size

Smaller chunks (256-512 tokens):
- Better for precise semantic search
- More granular analysis
- Higher processing cost

Larger chunks (1024-2048 tokens):
- Better for context understanding
- Lower processing cost
- May lose granularity

```python
CHUNK_SIZE = 1024  # Increase for larger chunks
CHUNK_OVERLAP = 100  # Increase overlap proportionally
```

### Custom Analysis Prompts

Modify the `prepare_for_ai_analysis` task:

```python
prepared_chunk = {
    **chunk,
    'analysis_prompt': f"""Custom prompt for your use case:

Requirements:
1. Extract specific information X
2. Identify pattern Y
3. Rate sentiment

Text:
{chunk['chunk_text']}"""
}
```

### Supporting Additional File Types

Extend the `extract_documents` task:

```python
import PyPDF2  # For PDF
from docx import Document  # For DOCX

def extract_text_from_file(blob_name, content):
    if blob_name.endswith('.pdf'):
        return extract_pdf(content)
    elif blob_name.endswith('.docx'):
        return extract_docx(content)
    else:
        return content.decode('utf-8')
```

## Troubleshooting

### Common Issues

**1. Token encoding errors**
```
Error: Failed to load encoding
Solution: Install tiktoken or use word-based fallback (already implemented)
```

**2. AI API rate limits**
```
Error: Rate limit exceeded
Solution: Implement exponential backoff in ai_analyzer.py
```

**3. BigQuery schema mismatch**
```
Error: Field X has incompatible type
Solution: Drop and recreate table, or alter schema to match
```

**4. GCS permission errors**
```
Error: Permission denied
Solution: Ensure service account has Storage Admin and BigQuery Admin roles
```

### Debugging

Enable verbose logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Check task logs in Airflow:
1. Go to DAG view
2. Click on task
3. View Logs tab

## Performance Optimization

### 1. Batch Processing

Process multiple chunks in parallel:

```python
from modules.ai_analyzer import create_analyzer

analyzer = create_analyzer(provider="openai")
analyzed = analyzer.batch_analyze(chunks, max_concurrent=10)
```

### 2. Caching

Cache tokenized documents to avoid re-tokenization:

```python
@task()
def tokenize_documents_cached(documents):
    # Check cache in GCS
    # If exists, return cached version
    # Else, tokenize and cache
```

### 3. Incremental Processing

Only process new documents:

```python
@task()
def extract_new_documents():
    # Query BigQuery for existing document sources
    # Only download new files from GCS
```

## Best Practices

1. **Document Organization**: Use clear naming conventions in GCS
2. **Monitoring**: Set up Airflow alerts for failures
3. **Cost Management**: Monitor AI API usage and BigQuery costs
4. **Data Quality**: Validate chunks before sending to AI
5. **Version Control**: Track prompt changes and results
6. **Backup**: Regularly backup BigQuery table

## Cost Estimation

**Per 1000 documents (avg 2000 tokens each):**

- Tokenization: Free (tiktoken)
- AI Analysis: Varies by provider
  - OpenAI GPT-3.5: ~$2-5
  - OpenAI GPT-4: ~$30-60
  - Vertex AI: ~$1-3
  - Local (Hugging Face): Free (compute only)
- BigQuery Storage: ~$0.02/GB/month
- BigQuery Queries: $5/TB processed

## Future Enhancements

- [ ] Add vector embeddings for semantic search
- [ ] Implement RAG (Retrieval Augmented Generation)
- [ ] Add support for images and multimodal content
- [ ] Create API endpoint for knowledge base queries
- [ ] Implement incremental updates
- [ ] Add data quality checks and validation
- [ ] Create visualization dashboard
- [ ] Implement automated reprocessing on prompt changes

## Support

For issues or questions:
1. Check Airflow logs
2. Review BigQuery execution details
3. Validate GCS permissions
4. Test AI provider connectivity

## License

[Your License Here]
