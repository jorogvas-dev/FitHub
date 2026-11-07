'use strict';

const generationForm = document.querySelector('[data-workout-generation]');
if (generationForm) {
  const button = generationForm.querySelector('button');
  const status = document.getElementById('generation-status');
  const label = button.textContent;
  window.addEventListener('pageshow', () => {
    generationForm.removeAttribute('aria-busy');
    button.disabled = false;
    button.textContent = label;
    status.hidden = true;
  });
  generationForm.addEventListener('submit', (event) => {
    if (generationForm.getAttribute('aria-busy') === 'true') {
      event.preventDefault();
      return;
    }
    generationForm.setAttribute('aria-busy', 'true');
    button.disabled = true;
    button.textContent = 'Generating draft…';
    status.hidden = false;
  });
}
