document.addEventListener("DOMContentLoaded",function(){
  document.querySelectorAll("[data-toggle-password]").forEach(function(button){
    var input=document.getElementById(button.getAttribute("data-toggle-password"));
    if(!input)return;
    button.addEventListener("click",function(){
      var visible=input.type==="password";
      input.type=visible?"text":"password";
      button.textContent=visible?"Hide":"Show";
      button.setAttribute("aria-pressed",String(visible));
      button.setAttribute("aria-label",visible?"Hide password":"Show password");
    });
  });
  var password=document.getElementById("password");
  var repeat=document.getElementById("password_confirm");
  var min=document.getElementById("password-rule-length");
  var same=document.getElementById("password-rule-match");
  function validate(){
    if(!password||!repeat)return;
    var matches=repeat.value!==""&&repeat.value===password.value;
    if(min)min.classList.toggle("met",password.value.length>=12);
    if(same)same.classList.toggle("met",matches);
    repeat.setCustomValidity(repeat.value&&!matches?"Passwords must match.":"");
  }
  if(password)password.addEventListener("input",validate);
  if(repeat)repeat.addEventListener("input",validate);
});
