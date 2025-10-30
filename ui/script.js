const refreshBtn = document.getElementById('refresh-btn');
const statusEl = document.getElementById('status');
const outputEl = document.getElementById('output');

const uploadForm = document.getElementById('upload-form');
const fileInput = document.getElementById('file-input');
const fileLabelText = document.getElementById('file-label-text');
const uploadBtn = document.getElementById('upload-btn');
const uploadStatus = document.getElementById('upload-status');
const uploadOutput = document.getElementById('upload-output');

function renderJson(target, data) {
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(data, null, 2);
  target.className = '';
  target.innerHTML = '';
  target.appendChild(pre);
}

function showEmptyState(target, message) {
  target.className = 'empty-state';
  target.textContent = message;
}

async function fetchExtractions() {
  if (!refreshBtn || !statusEl || !outputEl) {
    return;
  }

  refreshBtn.disabled = true;
  statusEl.textContent = 'Loading…';

  try {
    const response = await fetch('/getAllData', {
      headers: { Accept: 'application/json' },
    });

    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const data = await response.json();
    renderJson(outputEl, data);
    statusEl.textContent = `Fetched at ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    showEmptyState(outputEl, error.message);
    statusEl.textContent = 'Failed to load data';
  } finally {
    refreshBtn.disabled = false;
  }
}

async function submitExtraction(event) {
  event.preventDefault();
  if (!fileInput || !uploadBtn || !uploadStatus || !uploadOutput) {
    return;
  }

  if (!fileInput.files || fileInput.files.length === 0) {
    uploadStatus.textContent = 'Please choose a file before uploading.';
    return;
  }

  const file = fileInput.files[0];
  const formData = new FormData();
  formData.append('file', file);

  uploadBtn.disabled = true;
  uploadStatus.textContent = 'Uploading…';
  showEmptyState(uploadOutput, 'Processing extraction…');

  try {
    const response = await fetch('/extract/upload', {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`Extraction failed (status ${response.status})`);
    }
    const data = await response.json();
    renderJson(uploadOutput, data);
    uploadStatus.textContent = `Extracted ${file.name} at ${new Date().toLocaleTimeString()}`;
    fileInput.value = '';
    if (fileLabelText) {
      fileLabelText.textContent = 'Select a file…';
    }
    await fetchExtractions();
  } catch (error) {
    showEmptyState(uploadOutput, error.message);
    uploadStatus.textContent = 'Extraction failed.';
  } finally {
    uploadBtn.disabled = false;
  }
}

if (refreshBtn) {
  refreshBtn.addEventListener('click', fetchExtractions);
}

if (fileInput && fileLabelText) {
  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files.length > 0) {
      fileLabelText.textContent = fileInput.files[0].name;
    } else {
      fileLabelText.textContent = 'Select a file…';
    }
  });
}

if (uploadForm) {
  uploadForm.addEventListener('submit', submitExtraction);
}

fetchExtractions();
