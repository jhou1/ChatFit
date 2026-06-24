from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.runnables import RunnablePassthrough

import os
import glob

from prompts import RECIPE_ADVISOR_INSTRUCTION

def get_or_create_vector_store(docs_directory: str, persist_directory: str = "chroma.db"):
    embedding_model = GoogleGenerativeAIEmbeddings(model="gemini-embedding-2")

    if os.path.exists(persist_directory):
        return Chroma(persist_directory=persist_directory, embedding_function=embedding_model)

    # load documents
    docs_directory = os.path.expanduser(docs_directory)
    pattern = f"{docs_directory}/**/*.md"
    documents = []
    for file_path in glob.glob(pattern, recursive=True):
        with open(file_path, 'r') as file:
            content = file.read()

        doc = Document(
            page_content=content,
            metadata={"source": file_path}
        )
        documents.append(doc)

    # split to chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=200
    )
    chunks = text_splitter.split_documents(documents)

    # embed to vector store
    embedding_model = GoogleGenerativeAIEmbeddings(model="gemini-embedding-2")
    vector_store = Chroma.from_documents(
        documents=chunks,
        embedding=embedding_model,
        persist_directory=persist_directory
    )
    return vector_store

def create_recipe_rag_chain(vector_store, chat_model):
    retriever = vector_store.as_retriever()
    prompt_template = PromptTemplate.from_template(RECIPE_ADVISOR_INSTRUCTION)
    
    def format_docs(docs):
        return "\n\n".join(doc.page_content for doc in docs)
        
    rag_chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt_template
        | chat_model
    )
    
    return rag_chain
