// DOM Elements
const chatContainer = document.getElementById('chatContainer');
const emptyState = document.getElementById('emptyState');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const modelSelect = document.getElementById('modelSelect');
const settingsBtn = document.getElementById('settingsBtn');
const clearBtn = document.getElementById('clearBtn');
const notification = document.getElementById('notification');

// Settings modal elements
const settingsModal = document.getElementById('settingsModal');
const closeSettings = document.getElementById('closeSettings');
const apiKeyInput = document.getElementById('apiKeyInput');
const baseURLInput = document.getElementById('baseURLInput');
const defaultModelInput = document.getElementById('defaultModelInput');
const maxTokensInput = document.getElementById('maxTokensInput');
const testConnection = document.getElementById('testConnection');
const saveSettingsBtn = document.getElementById('saveSettings');

// State
let isLoading = false;
let currentAssistantMessage = null;
let settings = {};

// Initialize
async function init() {
  settings = await window.api.getSettings();
  updateSettingsUI();
  await loadModels();
  setupEventListeners();
  setupStreamListeners();
}

function updateSettingsUI() {
  apiKeyInput.value = settings.apiKey || '';
  baseURLInput.value = settings.baseURL || '';
  defaultModelInput.value = settings.model || '';
  maxTokensInput.value = settings.maxTokens || 4096;
}

async function loadModels() {
  const models = await window.api.fetchModels();
  if (models.length > 0) {
    modelSelect.innerHTML = models.map(m =>
      `<option value="${m}" ${m === settings.model ? 'selected' : ''}>${m}</option>`
    ).join('');
  }
}

function setupEventListeners() {
  // Send message
  sendBtn.addEventListener('click', handleSend);
  messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.metaKey) {
      e.preventDefault();
      handleSend();
    }
  });

  // Auto-resize textarea
  messageInput.addEventListener('input', () => {
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 150) + 'px';
  });

  // Settings
  settingsBtn.addEventListener('click', () => settingsModal.classList.remove('hidden'));
  closeSettings.addEventListener('click', () => settingsModal.classList.add('hidden'));
  settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) settingsModal.classList.add('hidden');
  });

  saveSettingsBtn.addEventListener('click', async () => {
    settings = await window.api.saveSettings({
      apiKey: apiKeyInput.value,
      baseURL: baseURLInput.value,
      model: defaultModelInput.value,
      maxTokens: parseInt(maxTokensInput.value) || 4096
    });
    settingsModal.classList.add('hidden');
    await loadModels();
  });

  testConnection.addEventListener('click', async () => {
    testConnection.textContent = 'Testing...';
    const models = await window.api.fetchModels();
    testConnection.textContent = models.length > 0
      ? `✓ Found ${models.length} models`
      : '✗ Connection failed';
    setTimeout(() => testConnection.textContent = 'Test Connection', 2000);
  });

  // Clear chat
  clearBtn.addEventListener('click', clearChat);

  // Menu events
  window.api.onNewChat(clearChat);
}

function setupStreamListeners() {
  window.api.onStreamChunk((text) => {
    if (currentAssistantMessage) {
      const content = currentAssistantMessage.textContent.replace('▊', '');
      currentAssistantMessage.textContent = content + text;
      scrollToBottom();
    }
  });

  window.api.onStreamEnd(() => {
    if (currentAssistantMessage) {
      currentAssistantMessage.classList.remove('streaming');
    }
    finishLoading();
  });

  window.api.onStreamError(async (error) => {
    if (error.type === 'model_not_available') {
      // Try fallback
      const models = await window.api.fetchModels();
      const fallback = models.find(m => m !== error.model);

      if (fallback) {
        showNotification(`Model "${error.model}" unavailable, using "${fallback}"`, 'info');
        // Remove the failed message
        if (currentAssistantMessage) {
          currentAssistantMessage.remove();
        }
        // Retry with fallback
        const lastUserMessage = [...chatContainer.querySelectorAll('.message.user')].pop();
        if (lastUserMessage) {
          currentAssistantMessage = createMessage('', 'assistant', true);
          window.api.sendMessage(lastUserMessage.textContent, fallback);
          return;
        }
      }
    }

    showNotification(error.message || 'An error occurred', 'error');
    if (currentAssistantMessage) {
      currentAssistantMessage.remove();
    }
    finishLoading();
  });
}

function handleSend() {
  if (isLoading) {
    // Cancel not implemented in this version
    return;
  }

  const content = messageInput.value.trim();
  if (!content) return;

  // Hide empty state
  emptyState.style.display = 'none';

  // Add user message
  createMessage(content, 'user');

  // Clear input
  messageInput.value = '';
  messageInput.style.height = 'auto';

  // Start loading
  isLoading = true;
  sendBtn.classList.add('loading');
  sendBtn.disabled = true;

  // Create assistant message placeholder
  currentAssistantMessage = createMessage('', 'assistant', true);

  // Send message
  window.api.sendMessage(content, modelSelect.value);
}

function createMessage(content, role, streaming = false) {
  const msg = document.createElement('div');
  msg.className = `message ${role}`;
  if (streaming) msg.classList.add('streaming');
  msg.textContent = content;
  chatContainer.appendChild(msg);
  scrollToBottom();
  return msg;
}

function finishLoading() {
  isLoading = false;
  sendBtn.classList.remove('loading');
  sendBtn.disabled = false;
  currentAssistantMessage = null;
  messageInput.focus();
}

function clearChat() {
  chatContainer.innerHTML = '';
  chatContainer.appendChild(emptyState);
  emptyState.style.display = 'flex';
  hideNotification();
}

function scrollToBottom() {
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function showNotification(message, type = 'info') {
  notification.textContent = message;
  notification.className = `notification ${type}`;

  const closeSpan = document.createElement('span');
  closeSpan.className = 'close';
  closeSpan.textContent = '×';
  closeSpan.onclick = hideNotification;
  notification.appendChild(closeSpan);
}

function hideNotification() {
  notification.className = 'notification hidden';
}

// Start
init();
