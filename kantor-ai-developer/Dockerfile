FROM python:3.12-slim

# Set up a new user named "user" with user ID 1000 (Required by Hugging Face Spaces)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# Set working directory
WORKDIR $HOME/app

# Copy project files
COPY --chown=user . $HOME/app

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port for Hugging Face Spaces
ENV PORT=7860
EXPOSE 7860

# Run the backend
CMD ["python", "main.py"]
