from .models import Branch, Company


def shell(request):
    branches = Branch.objects.filter(active=True)
    if request.user.is_authenticated and not request.user.is_superuser:
        branches = branches.filter(access__user=request.user)
    if not request.user.is_authenticated:
        branches = Branch.objects.none()
    current = branches.filter(pk=request.session.get("branch")).first() or branches.first()
    return {"company": Company.objects.first() or Company(), "branches": branches, "current_branch": current}
