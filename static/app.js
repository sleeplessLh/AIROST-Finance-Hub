function togglePassword(){const p=document.getElementById('password');const b=p.nextElementSibling;p.type=p.type==='password'?'text':'password';b.textContent=p.type==='password'?'Show':'Hide'}
function filterRows(value){document.querySelectorAll('#claimRows tr').forEach(r=>r.style.display=r.textContent.toLowerCase().includes(value.toLowerCase())?'':'none')}
function filterStatus(value){document.querySelectorAll('#claimRows tr[data-status]').forEach(r=>r.style.display=!value||r.dataset.status===value?'':'none')}
document.querySelectorAll('.dropzone input').forEach(input=>input.addEventListener('change',()=>{const label=input.closest('.dropzone');if(input.files[0]){label.classList.add('selected');label.querySelector('em').textContent=input.files[0].name}}));

