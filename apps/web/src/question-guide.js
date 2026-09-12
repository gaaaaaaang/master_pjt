export const GUIDE_FABS = ['FAB10', 'FAB11', 'FAB12', 'FAB13'];

// The guide supplies questions, never answers or hidden requests.
export const QUESTION_FLOWS = [
  {
    id: 'status', title: '현황에서 상세로', description: '전체 현황을 확인하고 한 공정으로 범위를 좁혀요.',
    steps: [
      { type: '현황', prompt: '{fab} 전체의 현재 WIP은 몇 개야?' },
      { type: '현황', prompt: '{fab} etch 공정의 현재 수율과 가동률은 얼마야?' },
      { type: '후속 질문', prompt: '같은 공정의 WIP도 알려줘', followsPrevious: true },
    ],
  },
  {
    id: 'diagnosis', title: '변화의 원인과 대응', description: '변화를 살펴보고 문서의 원인 후보와 점검 순서를 확인해요.',
    steps: [
      { type: '추세·비교', prompt: '{fab} etch 공정의 최근 24시간 Queue Time 추세를 보여줘' },
      { type: '진단', prompt: '왜 같은 공정의 Queue Time이 늘었어? 데이터와 문서 근거로 원인 후보를 설명해줘', followsPrevious: true },
      { type: '대응 절차', prompt: '{fab} etch 공정 Queue Time이 길어졌을 때 운영자가 어떤 순서로 점검하고 대응하면 좋을지 문서 근거로 알려줘' },
    ],
  },
  {
    id: 'impact', title: '가정이 바뀌면', description: '기준값을 조회한 뒤 가동률 변화에 따른 1차 처리량 추정을 확인해요.',
    steps: [
      { type: '현황', prompt: '{fab} etch 공정의 현재 가동률과 완료 LOT를 알려줘' },
      { type: '영향 추정', prompt: '그 공정 가동률이 5%p 떨어지면 처리량에 얼마나 영향이 있어?', followsPrevious: true },
    ],
  },
  {
    id: 'trend', title: '기간과 공정 비교', description: '추세에서 관심 공정을 찾고 현재 값과 주간 차이를 이어서 봐요.',
    steps: [
      { type: '추세·비교', prompt: '{fab} 최근 7일 공정별 수율 추세를 그래프로 보여줘' },
      { type: '후속 질문', prompt: '그 기간 평균 수율이 가장 낮은 공정을 알려줘', followsPrevious: true },
      { type: '후속 질문', prompt: '그 공정의 현재 WIP을 알려줘', followsPrevious: true },
      { type: '추세·비교', prompt: '{fab} 공정별 평균 WIP을 지난주와 이번주로 비교해줘' },
    ],
  },
  {
    id: 'knowledge', title: '공정 개념 이해', description: '지표 간 관계를 문서의 설명과 가정으로 이해해요.',
    steps: [
      { type: '공정 지식', prompt: 'Cycle Time Degradation이 뭐야? WIP이나 가동률과 어떤 관계가 있어? 문서 근거로 설명해줘' },
    ],
  },
];

export function guideQuestion(step, fab) {
  const value = String(fab).trim().toUpperCase();
  if (!GUIDE_FABS.includes(value)) throw new Error('FAB10~13 중 하나를 선택해 주세요.');
  return step.prompt.replaceAll('{fab}', value);
}
