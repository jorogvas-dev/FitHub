'use strict';
const nutritionForm = document.querySelector('[data-nutrition-generation]');
if (nutritionForm) {
  const button = nutritionForm.querySelector('button');
  const status = document.getElementById('review-status');
  const label = button.textContent;
  window.addEventListener('pageshow', () => {
    nutritionForm.removeAttribute('aria-busy');
    button.disabled = false;
    button.textContent = label;
    status.hidden = true;
  });
  nutritionForm.addEventListener('submit', (event) => {
    if (nutritionForm.getAttribute('aria-busy') === 'true') {
      event.preventDefault();
      return;
    }
    nutritionForm.setAttribute('aria-busy', 'true');
    button.disabled = true;
    button.textContent = 'Generating…';
    status.hidden = false;
  });
}
