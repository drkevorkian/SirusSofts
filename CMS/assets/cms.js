/* No dependencies. Small mobile navigation and destructive-action confirmations. */
(() => {
  "use strict";
  const toggle=document.querySelector("[data-nav-toggle]");
  const nav=document.getElementById("primaryNav");
  if(toggle&&nav){
    toggle.addEventListener("click",()=>{
      const open=toggle.getAttribute("aria-expanded")!=="true";
      toggle.setAttribute("aria-expanded",String(open));
      nav.classList.toggle("is-open",open);
    });
  }
  document.querySelectorAll("form[data-confirm]").forEach(form=>{
    form.addEventListener("submit",event=>{
      if(!window.confirm(form.dataset.confirm||"Confirm this action?"))
        event.preventDefault();
    });
  });
})();
